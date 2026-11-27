"""Automatic loop parallelisation: split one loop of a function into jobs.

Given an ordinary function such as::

    def sweep(configs, data):
        scores = []
        for config in configs:
            model = fit(config, data)
            scores.append(evaluate(model, data))
        return max(scores)

this module works out, from the function's source, whether the iterations of
its loop can run independently, and if so rewrites the function into three
pieces that together compute exactly what the original would have:

``pre``
    everything before the loop, run once. It returns the values the rest of
    the function needs (the *environment*) and the items to iterate over.
``body``
    the loop body, run over one *chunk* of the items. Chunks are what get
    farmed out: to worker processes, to SSH jobs, to a SLURM array.
``post``
    everything after the loop, run once on the merged outcome.

The rule that makes this sound
------------------------------

Iterations are independent when nothing one iteration does can be seen by
another. The analysis therefore sorts every name the loop body touches into
one of three kinds, and refuses the loop if a name fits none of them:

*iteration-local*
    assigned in the body before it is read there, and not used after the
    loop. Each iteration has its own.
*read-only*
    defined before the loop and only read in it.
*accumulator*
    defined before the loop and only ever **written** in it, through
    statements whose effect can be recorded and replayed: ``acc.append(x)``
    (any method call whose result is discarded), ``acc[k] = v``,
    ``acc.attr = v``, ``acc += v`` and ``acc = acc + v``. The body does not
    perform these; it logs them. ``post`` replays the log in the original
    iteration order on the real object. Because the replay is sequential and
    in order, the result is identical to a sequential run for *any* type --
    no assumption that an operation is associative, and no reordering of
    floating-point sums.

Anything else -- a value carried from one iteration to the next, ``break``,
``return`` inside the loop, a name read after the loop that the loop
assigned, a write to a global -- is a *blocker*, reported with its line and
reason by :meth:`ParallelPlan.explain`, and the function runs unsplit.

What cannot be proved from source
---------------------------------

Static analysis of Python can show that a loop is unsafe to split; it can
never show that one is safe, because it cannot see what a called function
does. So the static rule is only the first gate. Three checks that need real
values run behind it:

* **Aliasing.** After ``pre``, the values the body reads are searched for a
  reference to any object the loop collects into. ``seen = out`` followed by
  ``out.append(f(seen, x))`` passes the static rule and is caught here,
  before a single chunk is sent anywhere.
* **Mutation.** The body fingerprints every value it reads, and the items it
  was given, before and after its chunk. A difference means an iteration
  changed something its neighbours share.
* **Hidden randomness.** The state of the process-wide generators of
  ``random`` and NumPy is compared around each chunk, which catches a helper
  that draws from them. (Using them directly in the body is refused
  statically, unless the body seeds them first.)

When a check fails the split is abandoned and the original loop runs once,
in order, on the environment ``pre`` already produced -- ``pre`` itself is
never run twice -- so the answer is the sequential one either way.

What no check can see: state that lives outside the values the loop was
handed. A callee that updates a module-level cache, a library's internal
counter, a file, a database or a GPU's random state passes all of the above.
Side effects of that kind still happen once per iteration, but in an
unspecified order across chunks. That is the residual assumption of
splitting a loop, and it is the user's to accept.

The generated functions reference nothing from clustrix, so a worker that
does not have clustrix installed can run them.
"""

import ast
import builtins
import copy
import inspect
import textwrap
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

#: Prefix of every name this module introduces into generated code.
_P = "__cx_"

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
#: ``try``/``except*`` is its own node type, and only from Python 3.11.
_TRY_STATEMENTS: Tuple[type, ...] = tuple(
    node for node in (ast.Try, getattr(ast, "TryStar", None)) if node is not None
)

#: Module-level functions that draw from a hidden, process-wide generator.
_GLOBAL_RNG_MODULES = ("random", "np.random", "numpy.random")
#: Attributes of those modules that do *not* use the hidden generator.
_RNG_CONSTRUCTORS = frozenset(
    {
        "Random",
        "SystemRandom",
        "default_rng",
        "Generator",
        "RandomState",
        "SeedSequence",
        "PCG64",
        "Philox",
        "MT19937",
        "SFC64",
        "BitGenerator",
    }
)
_RNG_SEEDERS = frozenset({"seed"})

#: Builtins that build an iterable from their arguments without doing work
#: that belongs on the cluster. Used to decide whether ``pre`` may run where
#: the caller is rather than as a job of its own.
_CHEAP_BUILTINS = frozenset(
    {
        "range",
        "len",
        "enumerate",
        "zip",
        "list",
        "tuple",
        "dict",
        "set",
        "sorted",
        "reversed",
        "sum",
        "min",
        "max",
        "abs",
        "int",
        "float",
        "str",
        "bool",
        "round",
    }
)
_CHEAP_METHODS = frozenset({"items", "keys", "values"})

#: How two things that are not variables are named when a chunk reports that
#: it changed them.
_ITEMS = "the items being looped over"
_RNG = "the global random number generator"

#: Mutable builtin containers. A *global* of one of these types that the loop
#: writes to is a blocker: the write would land in a worker and be lost.
_MUTABLE_CONTAINERS = (list, dict, set, bytearray)


@dataclass(frozen=True)
class Blocker:
    """One reason a loop cannot be split, and where."""

    line: int
    reason: str


@dataclass
class LoopAnalysis:
    """What the analysis concluded about one candidate loop."""

    line: int
    header: str
    accumulators: List[str] = field(default_factory=list)
    read_only: List[str] = field(default_factory=list)
    iteration_local: List[str] = field(default_factory=list)
    blockers: List[Blocker] = field(default_factory=list)

    @property
    def parallelizable(self) -> bool:
        return not self.blockers


@dataclass
class ParallelPlan:
    """The outcome of analysing a function: a split, or the reasons for none."""

    func_name: str
    loops: List[LoopAnalysis] = field(default_factory=list)
    #: Why no loop was even considered (no source, a generator, ...).
    unavailable: Optional[str] = None
    chosen: Optional[LoopAnalysis] = None
    code: Optional[types.CodeType] = None
    #: True when ``pre`` only unpacks arguments, so it can run in the caller.
    pre_is_cheap: bool = False
    #: True when ``post`` only replays and returns, so it can run in the caller.
    post_is_cheap: bool = False
    body_names: Tuple[str, ...] = ()
    post_names: Tuple[str, ...] = ()

    @property
    def parallelizable(self) -> bool:
        return self.chosen is not None

    def explain(self) -> str:
        """A plain-text account of the decision, for the person who asked."""
        lines = [f"{self.func_name}:"]
        if self.unavailable:
            lines.append(f"  not analysed: {self.unavailable}")
            return "\n".join(lines)
        if not self.loops:
            lines.append("  no loop at the top level of the function to split")
            return "\n".join(lines)
        for loop in self.loops:
            verdict = "SPLIT" if loop is self.chosen else "not split"
            if loop.parallelizable and loop is not self.chosen:
                verdict = "could be split (an earlier loop was chosen)"
            lines.append(f"  line {loop.line}: `{loop.header}` -- {verdict}")
            for blocker in loop.blockers:
                lines.append(f"      line {blocker.line}: {blocker.reason}")
            if loop.parallelizable:
                if loop.accumulators:
                    lines.append(
                        "      collected in order: " + ", ".join(loop.accumulators)
                    )
                if loop.read_only:
                    lines.append(
                        "      read-only (checked at run time): "
                        + ", ".join(loop.read_only)
                    )
                if loop.iteration_local:
                    lines.append(
                        "      private to each iteration: "
                        + ", ".join(loop.iteration_local)
                    )
        if self.chosen is not None:
            where_pre = "in the caller" if self.pre_is_cheap else "as its own job"
            where_post = "in the caller" if self.post_is_cheap else "as its own job"
            lines.append(f"  code before the loop runs {where_pre}")
            lines.append(f"  code after the loop runs {where_post}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Name bookkeeping
# --------------------------------------------------------------------------


class _Stores(ast.NodeVisitor):
    """Names bound in the *current* function scope by some statements."""

    def __init__(self) -> None:
        self.names: Set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def _definition(self, node: Any) -> None:
        # The name is bound here; the body is another scope.
        self.names.add(node.name)

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def _comprehension(self, node: ast.AST) -> None:
        # Comprehension targets are private to it; a walrus inside one is not.
        for sub in ast.walk(node):
            if isinstance(sub, ast.NamedExpr):
                self.names.add(sub.target.id)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.names.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.names.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.names.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)


def _stored(nodes: Sequence[ast.AST]) -> Set[str]:
    visitor = _Stores()
    for node in nodes:
        visitor.visit(node)
    return visitor.names


def _loaded(nodes: Sequence[ast.AST]) -> Set[str]:
    """Every name read anywhere under ``nodes``, nested scopes included."""
    return {
        sub.id
        for node in nodes
        for sub in ast.walk(node)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
    }


def _occurrences(nodes: Sequence[ast.AST], name: str) -> int:
    return sum(
        1
        for node in nodes
        for sub in ast.walk(node)
        if isinstance(sub, ast.Name) and sub.id == name
    )


def _statements(body: Sequence[ast.stmt]):
    """Every statement in ``body`` at any control-flow depth, this scope only."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, _NESTED_SCOPES):
            continue
        for name in ("body", "orelse", "finalbody"):
            yield from _statements(getattr(stmt, name, []) or [])
        for handler in getattr(stmt, "handlers", []) or []:
            yield from _statements(handler.body)
        for case in getattr(stmt, "cases", []) or []:
            yield from _statements(case.body)


def _in_scope(node: ast.AST):
    """Walk ``node`` without entering nested function, lambda or class bodies."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(child, _NESTED_SCOPES):
                continue
            stack.append(child)


class _ReadBeforeAssigned:
    """Find names a block may read before it has assigned them.

    A name the loop body assigns is private to an iteration only if every
    read of it follows an assignment *in the same iteration*. Otherwise the
    read sees the previous iteration's value, which is a dependency between
    iterations. The check is conservative: where control flow makes the
    answer uncertain it reports the name.

    ``block`` and ``stmt`` return the names definitely assigned afterwards,
    or ``None`` when control cannot continue past that point (``continue``,
    ``break``, ``return``, ``raise``) -- such a path contributes nothing to
    what is known after an ``if`` or a ``try``.
    """

    def __init__(self, tracked: Set[str]):
        self.tracked = tracked
        self.violations: Dict[str, int] = {}

    def _read(self, node: ast.AST, assigned: Set[str]) -> None:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Name)
                and isinstance(sub.ctx, ast.Load)
                and sub.id in self.tracked
                and sub.id not in assigned
            ):
                self.violations.setdefault(sub.id, getattr(sub, "lineno", 0))

    def _bind(self, target: ast.AST, assigned: Set[str]) -> Set[str]:
        # Index and attribute expressions inside a target are reads.
        for sub in ast.walk(target):
            if isinstance(sub, (ast.Subscript, ast.Attribute)):
                self._read(sub, assigned)
        return assigned | _stored([target])

    @staticmethod
    def _join(paths: Sequence[Optional[Set[str]]]) -> Optional[Set[str]]:
        """What is assigned on *every* path that carries on."""
        live = [path for path in paths if path is not None]
        return set.intersection(*live) if live else None

    def block(
        self, stmts: Sequence[ast.stmt], assigned: Set[str]
    ) -> Optional[Set[str]]:
        current: Optional[Set[str]] = assigned
        for stmt in stmts:
            if current is None:
                # Unreachable code is still checked, against the last state
                # that was known, so nothing in it goes unexamined.
                self.stmt(stmt, set(assigned))
                continue
            current = self.stmt(stmt, current)
        return current

    def stmt(self, node: ast.stmt, assigned: Set[str]) -> Optional[Set[str]]:
        if isinstance(node, (ast.Continue, ast.Break)):
            return None
        if isinstance(node, (ast.Return, ast.Raise)):
            for child in ast.iter_child_nodes(node):
                self._read(child, assigned)
            return None
        if isinstance(node, ast.Assign):
            self._read(node.value, assigned)
            for target in node.targets:
                assigned = self._bind(target, assigned)
            return assigned
        if isinstance(node, ast.AugAssign):
            self._read(node.target, assigned)
            for sub in ast.walk(node.target):
                # An augmented target is read before it is written.
                if isinstance(sub, ast.Name) and sub.id in self.tracked:
                    if sub.id not in assigned:
                        self.violations.setdefault(sub.id, node.lineno)
            self._read(node.value, assigned)
            return assigned | _stored([node.target])
        if isinstance(node, ast.AnnAssign):
            if node.value is None:
                return assigned
            self._read(node.value, assigned)
            return self._bind(node.target, assigned)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._read(node.iter, assigned)
            self.block(node.body, self._bind(node.target, set(assigned)))
            self.block(node.orelse, set(assigned))
            return assigned  # the loop may run zero times
        if isinstance(node, ast.While):
            self._read(node.test, assigned)
            self.block(node.body, set(assigned))
            self.block(node.orelse, set(assigned))
            return assigned
        if isinstance(node, ast.If):
            self._read(node.test, assigned)
            return self._join(
                [
                    self.block(node.body, set(assigned)),
                    self.block(node.orelse, set(assigned)),
                ]
            )
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self._read(item.context_expr, assigned)
                if item.optional_vars is not None:
                    assigned = self._bind(item.optional_vars, assigned)
            return self.block(node.body, assigned)
        if isinstance(node, _TRY_STATEMENTS):
            # ast.Try and ast.TryStar have the same four fields.
            attempt: Any = node
            tried = self.block(attempt.body, set(assigned))
            paths = [None if tried is None else self.block(attempt.orelse, set(tried))]
            for handler in attempt.handlers:
                # The body may have raised before assigning anything.
                start = set(assigned)
                if handler.type is not None:
                    self._read(handler.type, assigned)
                if handler.name:
                    start.add(handler.name)
                paths.append(self.block(handler.body, start))
            joined = self._join(paths)
            # finalbody runs on every path, including one that assigned
            # nothing, so it is checked against the entry state.
            finally_ = self.block(attempt.finalbody, set(assigned))
            if joined is None or finally_ is None:
                return None
            return joined | (finally_ - assigned)
        if isinstance(node, _NESTED_SCOPES):
            # The nested body runs later, but nothing says when: require
            # whatever it reads to be assigned already.
            self._read(node, assigned)
            return assigned | {node.name}
        if isinstance(node, ast.Delete):
            for target in node.targets:
                self._read(target, assigned)
            return assigned - _stored(node.targets)
        if isinstance(node, ast.Match):
            self._read(node.subject, assigned)
            for case in node.cases:
                start = assigned | _stored([case.pattern])
                if case.guard is not None:
                    self._read(case.guard, start)
                self.block(case.body, start)
            return assigned  # no case may match
        # Expr, Assert, Import, Pass, Global, ...
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._read(child, assigned)
        return assigned | _stored([node])


# --------------------------------------------------------------------------
# Accumulator sites
# --------------------------------------------------------------------------


class _NotASite(Exception):
    """The statement is not a recordable write."""


@dataclass
class _Site:
    """One statement that writes to an accumulator.

    ``payloads`` are the expressions the body evaluates and logs;
    ``replay`` is the original statement with each of them replaced by a
    reference into the logged record, for ``post`` to execute.
    """

    stmt: ast.stmt
    root: str
    payloads: List[ast.expr]
    replay: ast.stmt


def _slice_as_expression(node: ast.expr) -> ast.expr:
    """Turn subscript syntax into a value that can be logged: ``a:b`` → slice."""
    if isinstance(node, ast.Slice):
        none = ast.Constant(value=None)
        return ast.Call(
            func=ast.Name(id="slice", ctx=ast.Load()),
            args=[node.lower or none, node.upper or none, node.step or none],
            keywords=[],
        )
    if isinstance(node, ast.Tuple):
        return ast.Tuple(
            elts=[_slice_as_expression(element) for element in node.elts],
            ctx=ast.Load(),
        )
    return node


def _site_for(stmt: ast.stmt) -> _Site:
    """Describe ``stmt`` as a recordable write, or raise :class:`_NotASite`."""
    payloads: List[ast.expr] = []

    def slot(expression: ast.expr) -> ast.expr:
        payloads.append(expression)
        return ast.Subscript(
            value=ast.Name(id=f"{_P}op", ctx=ast.Load()),
            slice=ast.Constant(value=len(payloads)),
            ctx=ast.Load(),
        )

    def chain(node: ast.expr, ctx: ast.expr_context) -> Tuple[str, ast.expr]:
        """Rebuild ``root[.attr|[index]]...`` with indexes taken from the log."""
        if isinstance(node, ast.Name):
            return node.id, ast.Name(id=node.id, ctx=ctx)
        if isinstance(node, ast.Attribute):
            root, value = chain(node.value, ast.Load())
            return root, ast.Attribute(value=value, attr=node.attr, ctx=ctx)
        if isinstance(node, ast.Subscript):
            root, value = chain(node.value, ast.Load())
            index = slot(_slice_as_expression(node.slice))
            return root, ast.Subscript(value=value, slice=index, ctx=ctx)
        raise _NotASite

    replay: ast.stmt
    expected_root_mentions = 1

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        if not isinstance(call.func, ast.Attribute):
            raise _NotASite
        root, receiver = chain(call.func.value, ast.Load())
        positional = slot(ast.Tuple(elts=list(call.args), ctx=ast.Load()))
        keyword = slot(
            ast.Dict(
                keys=[
                    None if kw.arg is None else ast.Constant(value=kw.arg)
                    for kw in call.keywords
                ],
                values=[kw.value for kw in call.keywords],
            )
        )
        replay = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=receiver, attr=call.func.attr, ctx=ast.Load()),
                args=[ast.Starred(value=positional, ctx=ast.Load())],
                keywords=[ast.keyword(arg=None, value=keyword)],
            )
        )
    elif isinstance(stmt, ast.AugAssign):
        root, target = chain(stmt.target, ast.Store())
        assert isinstance(target, (ast.Name, ast.Attribute, ast.Subscript))
        replay = ast.AugAssign(target=target, op=stmt.op, value=slot(stmt.value))
    elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target_node = stmt.targets[0]
        if isinstance(target_node, (ast.Subscript, ast.Attribute)):
            root, target = chain(target_node, ast.Store())
            replay = ast.Assign(targets=[target], value=slot(stmt.value))
        elif isinstance(target_node, ast.Name) and isinstance(stmt.value, ast.BinOp):
            # acc = acc + value   or   acc = value + acc
            root = target_node.id
            left, right = stmt.value.left, stmt.value.right

            def is_root(node: ast.expr) -> bool:
                return isinstance(node, ast.Name) and node.id == root

            current: ast.expr = ast.Name(id=root, ctx=ast.Load())
            if is_root(left) and not is_root(right):
                combined = ast.BinOp(left=current, op=stmt.value.op, right=slot(right))
            elif is_root(right) and not is_root(left):
                combined = ast.BinOp(left=slot(left), op=stmt.value.op, right=current)
            else:
                raise _NotASite
            replay = ast.Assign(
                targets=[ast.Name(id=root, ctx=ast.Store())], value=combined
            )
            expected_root_mentions = 2
        else:
            raise _NotASite
    else:
        raise _NotASite

    # The accumulator may appear only as the thing written to. If it also
    # appears in what is written, the write depends on earlier iterations.
    if _occurrences([stmt], root) != expected_root_mentions:
        raise _NotASite
    return _Site(stmt=stmt, root=root, payloads=payloads, replay=replay)


def _mutation_root(stmt: ast.stmt) -> Optional[str]:
    """The name ``stmt`` modifies in place, if it has that shape at all."""

    def root_of(node: ast.expr) -> Optional[str]:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        if isinstance(stmt.value.func, ast.Attribute):
            return root_of(stmt.value.func.value)
        return None
    if isinstance(stmt, ast.AugAssign):
        if isinstance(stmt.target, (ast.Subscript, ast.Attribute)):
            return root_of(stmt.target)
        return None
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, (ast.Subscript, ast.Attribute)):
                return root_of(target)
    return None


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


def _dotted(node: ast.expr) -> Optional[str]:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _global_rng_calls(body: Sequence[ast.stmt]) -> List[Tuple[int, int, str, str]]:
    """Calls into a process-wide random generator, in source order."""
    calls = []
    for stmt in body:
        for node in _in_scope(stmt):
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                if name is None or "." not in name:
                    continue
                module, _, attribute = name.rpartition(".")
                if module in _GLOBAL_RNG_MODULES:
                    if attribute not in _RNG_CONSTRUCTORS:
                        calls.append((node.lineno, node.col_offset, name, attribute))
    return sorted(calls)


def _body_seeds_global_rng(body: Sequence[ast.stmt]) -> bool:
    """True when the first thing the body does with such a generator is seed it.

    What follows a seed is determined by the iteration alone.
    """
    calls = _global_rng_calls(body)
    return bool(calls) and calls[0][3] in _RNG_SEEDERS


def _global_rng_blockers(body: Sequence[ast.stmt]) -> List[Blocker]:
    """Uses of a process-wide random generator that the body did not seed."""
    calls = _global_rng_calls(body)
    if not calls or _body_seeds_global_rng(body):
        return []
    line, _, name, _ = calls[0]
    return [
        Blocker(
            line,
            f"{name}() draws from a random generator shared by all iterations, "
            "so each draw depends on the ones before it; create one per "
            "iteration instead, e.g. rng = np.random.default_rng(seed + i)",
        )
    ]


def _analyse_loop(
    loop: ast.For,
    before: Sequence[ast.stmt],
    after: Sequence[ast.stmt],
    parameters: Set[str],
    declared_outer: Set[str],
    resolve: Callable[[str], Tuple[bool, Any]],
) -> Tuple[LoopAnalysis, List[_Site]]:
    header = f"for {ast.unparse(loop.target)} in {ast.unparse(loop.iter)}"
    analysis = LoopAnalysis(line=loop.lineno, header=header)
    block = analysis.blockers
    body = loop.body

    if loop.orelse:
        block.append(Blocker(loop.orelse[0].lineno, "the loop has an else clause"))

    # -- statements that end or escape the loop ------------------------------
    for stmt in _statements(body):
        if isinstance(stmt, ast.Return):
            block.append(Blocker(stmt.lineno, "the loop body returns"))
        elif isinstance(stmt, (ast.Global, ast.Nonlocal)):
            block.append(
                Blocker(stmt.lineno, "the loop body declares global/nonlocal names")
            )
    for stmt in body:
        for node in _in_scope(stmt):
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
                block.append(Blocker(node.lineno, "the loop body yields or awaits"))

    def breaks(stmts: Sequence[ast.stmt]):
        # A break inside a nested loop ends that loop, not this one.
        for stmt in stmts:
            if isinstance(stmt, ast.Break):
                yield stmt
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                yield from breaks(stmt.orelse)
            elif not isinstance(stmt, _NESTED_SCOPES):
                for name in ("body", "orelse", "finalbody"):
                    yield from breaks(getattr(stmt, name, []) or [])
                for handler in getattr(stmt, "handlers", []) or []:
                    yield from breaks(handler.body)
                for case in getattr(stmt, "cases", []) or []:
                    yield from breaks(case.body)

    for stmt in breaks(body):
        block.append(
            Blocker(
                stmt.lineno,
                "break stops the loop early, so later iterations depend on "
                "earlier ones",
            )
        )

    block.extend(_global_rng_blockers(body))

    # -- classify names -------------------------------------------------------
    defined_before = parameters | _stored(before)
    target_names = _stored([loop.target])
    assigned_in_body = _stored(body) | target_names
    read_in_body = _loaded(body)
    read_after = _loaded(after)
    read_in_header = _loaded([loop.iter])

    for name in sorted(assigned_in_body & declared_outer):
        block.append(
            Blocker(loop.lineno, f"{name} is a global or nonlocal the loop assigns")
        )

    # A module imported, or a function or class defined, before the loop is
    # code rather than data: `np.save(...)` is a call into numpy, not a write
    # to an object called np.
    code_names = {
        name
        for stmt in before
        if isinstance(stmt, (ast.Import, ast.ImportFrom, *_NESTED_SCOPES))
        for name in _stored([stmt])
    } - _stored(
        [
            stmt
            for stmt in before
            if not isinstance(stmt, (ast.Import, ast.ImportFrom, *_NESTED_SCOPES))
        ]
    )

    sites: List[_Site] = []
    candidates: Dict[str, List[_Site]] = {}
    mutated: Dict[str, int] = {}
    for stmt in _statements(body):
        root = _mutation_root(stmt)
        if root is None and not isinstance(stmt, (ast.Assign, ast.AugAssign)):
            continue
        try:
            site = _site_for(stmt)
        except _NotASite:
            if root is not None and root not in code_names:
                mutated.setdefault(root, stmt.lineno)
            continue
        if site.root in code_names:
            continue
        candidates.setdefault(site.root, []).append(site)
        mutated.setdefault(site.root, stmt.lineno)

    accumulators: Set[str] = set()
    for name, name_sites in candidates.items():
        if name not in defined_before or name in target_names:
            continue
        mentions = sum(
            (
                2
                if isinstance(s.stmt, ast.Assign)
                and isinstance(s.stmt.targets[0], ast.Name)
                else 1
            )
            for s in name_sites
        )
        if _occurrences(body, name) != mentions or name in read_in_header:
            continue
        accumulators.add(name)
        sites.extend(name_sites)

    for name, line in sorted(mutated.items()):
        if name in accumulators:
            continue
        if name in defined_before:
            if name in assigned_in_body and name not in read_after:
                # Reassigned in the body: judged below as iteration-local.
                continue
            block.append(
                Blocker(
                    line,
                    f"{name} is modified in the loop and also read in it, so an "
                    "iteration can see what an earlier one did",
                )
            )
        elif name not in assigned_in_body:
            found, value = resolve(name)
            if found and isinstance(value, _MUTABLE_CONTAINERS):
                block.append(
                    Blocker(
                        line,
                        f"{name} is a global the loop writes to; a worker's copy "
                        "would be lost. Create it inside the function and "
                        "return it",
                    )
                )

    private = assigned_in_body - accumulators
    checker = _ReadBeforeAssigned(private - target_names)
    checker.block(body, set(target_names))
    for name in sorted(private):
        if name in checker.violations:
            block.append(
                Blocker(
                    checker.violations[name],
                    f"{name} is read before the iteration assigns it, so it "
                    "carries a value from the previous iteration",
                )
            )
        elif name in read_after:
            block.append(
                Blocker(
                    loop.lineno,
                    f"{name} is assigned in the loop and used after it, which "
                    "depends on which iteration ran last",
                )
            )

    analysis.accumulators = sorted(accumulators)
    analysis.iteration_local = sorted(private - set(checker.violations))
    analysis.read_only = sorted(
        (read_in_body & defined_before) - assigned_in_body - accumulators
    )
    return analysis, sites


# --------------------------------------------------------------------------
# Rewriting
# --------------------------------------------------------------------------


class _ReturnsAsTuples(ast.NodeTransformer):
    """In ``pre``: a ``return`` means the function finished before the loop."""

    def visit_Return(self, node: ast.Return) -> ast.Return:
        value = node.value or ast.Constant(value=None)
        return ast.copy_location(
            ast.Return(
                value=ast.Tuple(
                    elts=[ast.Constant(value="return"), value], ctx=ast.Load()
                )
            ),
            node,
        )

    def _skip(self, node: ast.AST) -> ast.AST:
        return node

    visit_FunctionDef = _skip
    visit_AsyncFunctionDef = _skip
    visit_Lambda = _skip
    visit_ClassDef = _skip


class _LogWrites(ast.NodeTransformer):
    """In ``body``: replace each accumulator write with a log record."""

    def __init__(self, site_numbers: Dict[int, Tuple[int, _Site]]):
        self.site_numbers = site_numbers

    def generic_visit(self, node: ast.AST) -> ast.AST:
        entry = self.site_numbers.get(id(node))
        if entry is None:
            return super().generic_visit(node)
        number, site = entry
        record = ast.Tuple(
            elts=[ast.Constant(value=number), *site.payloads], ctx=ast.Load()
        )
        logged = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id=f"{_P}log", ctx=ast.Load()),
                    attr="append",
                    ctx=ast.Load(),
                ),
                args=[record],
                keywords=[],
            )
        )
        return ast.copy_location(logged, node)


def _parse_statements(source: str) -> List[ast.stmt]:
    return ast.parse(textwrap.dedent(source)).body


def _bind_from_env(names: Sequence[str]) -> List[ast.stmt]:
    """``if 'x' in env: x = env['x']`` for each name.

    Conditional, because a name defined on only some paths before the loop
    may legitimately be absent; using it then raises ``NameError`` exactly as
    the original function would.
    """
    statements: List[ast.stmt] = []
    for name in names:
        statements.extend(
            _parse_statements(
                f"if {name!r} in {_P}env:\n    {name} = {_P}env[{name!r}]"
            )
        )
    return statements


#: Functions the generated code calls. They are compiled into the same
#: module as ``pre``/``body``/``post`` rather than imported, so a worker
#: without clustrix can still run them.
_HELPERS_SOURCE = f"""
def {_P}fingerprint({_P}env, {_P}names):
    import hashlib
    import pickle
    try:
        import cloudpickle as serializer
    except ImportError:
        serializer = pickle
    prints = {{}}
    for name in {_P}names:
        if name not in {_P}env:
            continue
        try:
            data = serializer.dumps({_P}env[name], protocol=4)
        except Exception:
            prints[name] = None
        else:
            prints[name] = hashlib.sha1(data).hexdigest()
    return prints


def {_P}rng_state():
    import hashlib
    import random
    import sys
    digest = hashlib.sha1(repr(random.getstate()).encode())
    numpy = sys.modules.get("numpy")
    if numpy is not None:
        try:
            state = numpy.random.get_state()
            digest.update(state[1].tobytes())
            digest.update(repr(state[2:]).encode())
        except Exception:
            pass
    return digest.hexdigest()


def {_P}aliases({_P}env, {_P}written, {_P}read):
    import gc
    import types
    immutable = (int, float, complex, str, bytes, bool, type(None), tuple,
                 frozenset, range)
    opaque = (types.ModuleType, type, str, bytes, int, float, complex, bool,
              type(None))
    targets = {{
        id({_P}env[name]): name
        for name in {_P}written
        if name in {_P}env and not isinstance({_P}env[name], immutable)
    }}
    found = set()
    if not targets:
        return []
    for name in {_P}read:
        if name not in {_P}env:
            continue
        seen = set()
        stack = [{_P}env[name]]
        while stack and len(seen) < 200000:
            obj = stack.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if id(obj) in targets:
                found.add((name, targets[id(obj)]))
                continue
            if isinstance(obj, opaque):
                continue
            if isinstance(obj, types.FunctionType):
                # Not its globals: that is the whole module.
                for cell in obj.__closure__ or ():
                    try:
                        stack.append(cell.cell_contents)
                    except ValueError:
                        pass
                stack.extend(obj.__defaults__ or ())
                stack.extend((obj.__kwdefaults__ or {{}}).values())
                continue
            if isinstance(obj, types.MethodType):
                stack.extend((obj.__self__, obj.__func__))
                continue
            stack.extend(gc.get_referents(obj))
    return sorted(found)
"""


def _generate(
    function: ast.FunctionDef,
    loop: ast.For,
    before: Sequence[ast.stmt],
    after: Sequence[ast.stmt],
    analysis: LoopAnalysis,
    sites: List[_Site],
    parameters: List[str],
    defined_before: Set[str],
) -> Tuple[ast.Module, Tuple[str, ...], Tuple[str, ...]]:
    accumulators = set(analysis.accumulators)
    # Seeded at the top of each iteration: the generator's state afterwards
    # says nothing about whether iterations depend on each other.
    watch_rng = not _body_seeds_global_rng(loop.body)
    body_names = tuple(sorted((_loaded(loop.body) & defined_before) - accumulators))
    post_names = tuple(sorted(((_loaded(after) & defined_before) | accumulators)))
    env_names = tuple(sorted(set(body_names) | set(post_names)))

    # -- pre ------------------------------------------------------------------
    pre_body: List[ast.stmt] = []
    for name in parameters:
        pre_body.extend(_parse_statements(f"{name} = {_P}args[{name!r}]"))
    transformer = _ReturnsAsTuples()
    pre_body.extend(transformer.visit(copy.deepcopy(stmt)) for stmt in before)
    pre_body.extend(_parse_statements(f"""
            {_P}items = {ast.unparse(loop.iter)}
            if not isinstance({_P}items, range):
                {_P}items = list({_P}items)
            {_P}scope = locals()
            {_P}found = {{{_P}n: {_P}scope[{_P}n] for {_P}n in {env_names!r} if {_P}n in {_P}scope}}
            return (
                "env",
                {_P}found,
                {_P}items,
                {_P}aliases({_P}found, {tuple(sorted(accumulators))!r}, {body_names!r}),
            )
            """))
    pre = _function(f"{_P}pre", [f"{_P}args"], pre_body)

    # -- body -----------------------------------------------------------------
    # Rewritten on a copy: the original statements are still being analysed
    # as the surroundings of any later loop.
    copies: Dict[int, Any] = {}
    body_copy = copy.deepcopy(loop.body, copies)
    numbers = {
        id(copies[id(site.stmt)]): (number, site) for number, site in enumerate(sites)
    }
    logged_body = [_LogWrites(numbers).visit(stmt) for stmt in body_copy]
    body_statements: List[ast.stmt] = _bind_from_env(body_names)
    body_statements.extend(_parse_statements(f"""
            {_P}watched = dict({_P}env)
            if not isinstance({_P}items, range):
                {_P}watched[{_ITEMS!r}] = {_P}items
            {_P}names = ({_ITEMS!r},) + {body_names!r}
            {_P}before = {_P}fingerprint({_P}watched, {_P}names)
            {_P}rng_before = {_P}rng_state() if {watch_rng!r} else None
            {_P}log = []
            """))
    body_statements.append(
        ast.For(
            target=copy.deepcopy(loop.target),
            iter=ast.Name(id=f"{_P}items", ctx=ast.Load()),
            body=logged_body,
            orelse=[],
        )
    )
    body_statements.extend(_parse_statements(f"""
            {_P}after = {_P}fingerprint({_P}watched, {_P}names)
            {_P}changed = sorted(n for n in {_P}before if {_P}before[n] != {_P}after[n])
            if {watch_rng!r} and {_P}rng_state() != {_P}rng_before:
                {_P}changed.append({_RNG!r})
            return (
                {_P}log,
                {_P}changed,
                sorted(n for n in {_P}before if {_P}before[n] is None),
            )
            """))
    body = _function(f"{_P}body", [f"{_P}env", f"{_P}items"], body_statements)

    # -- post -----------------------------------------------------------------
    post_statements: List[ast.stmt] = _bind_from_env(post_names)
    if sites:
        branches: Optional[ast.stmt] = None
        for number, site in reversed(list(enumerate(sites))):
            test = ast.Compare(
                left=ast.Subscript(
                    value=ast.Name(id=f"{_P}op", ctx=ast.Load()),
                    slice=ast.Constant(value=0),
                    ctx=ast.Load(),
                ),
                ops=[ast.Eq()],
                comparators=[ast.Constant(value=number)],
            )
            branches = ast.If(
                test=test,
                body=[site.replay],
                orelse=[branches] if branches is not None else [],
            )
        assert branches is not None
        post_statements.append(
            ast.For(
                target=ast.Name(id=f"{_P}op", ctx=ast.Store()),
                iter=ast.Name(id=f"{_P}log", ctx=ast.Load()),
                body=[branches],
                orelse=[],
            )
        )
    post_statements.extend(copy.deepcopy(stmt) for stmt in after)
    post = _function(f"{_P}post", [f"{_P}env", f"{_P}log"], post_statements)

    # -- rest: the original loop and what follows it, unsplit -----------------
    rest_statements: List[ast.stmt] = _bind_from_env(env_names)
    rest_statements.append(
        ast.For(
            target=copy.deepcopy(loop.target),
            iter=ast.Name(id=f"{_P}items", ctx=ast.Load()),
            body=copy.deepcopy(loop.body),
            orelse=[],
        )
    )
    rest_statements.extend(copy.deepcopy(stmt) for stmt in after)
    rest = _function(f"{_P}rest", [f"{_P}env", f"{_P}items"], rest_statements)

    module = ast.Module(
        body=[*_parse_statements(_HELPERS_SOURCE), pre, body, post, rest],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    return module, body_names, post_names


def _function(
    name: str, parameters: List[str], body: List[ast.stmt]
) -> ast.FunctionDef:
    function = _parse_statements(f"def {name}({', '.join(parameters)}):\n    pass")[0]
    assert isinstance(function, ast.FunctionDef)
    function.body = body or [ast.Pass()]
    return function


# --------------------------------------------------------------------------
# Comprehensions as loops
# --------------------------------------------------------------------------


def _comprehension_as_loop(stmt: ast.stmt, number: int) -> Optional[List[ast.stmt]]:
    """Rewrite ``x = [f(i) for i in xs]`` as an explicit loop, when equivalent.

    Only a statement that *is* one comprehension with one ``for`` is handled.
    The comprehension's variables become ordinary locals, so they are renamed
    to names nothing else can be using.
    """
    if isinstance(stmt, (ast.Assign, ast.Return, ast.AnnAssign)):
        value = stmt.value
    else:
        return None
    if not isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp)):
        return None
    if len(value.generators) != 1 or value.generators[0].is_async:
        return None
    generator = value.generators[0]
    targets = _stored([generator.target])

    inner: List[ast.AST] = [*generator.ifs]
    if isinstance(value, ast.DictComp):
        inner.extend([value.key, value.value])
    else:
        inner.append(value.elt)
    for node in inner:
        for sub in ast.walk(node):
            # A nested scope that rebinds one of the names would make a
            # blanket rename wrong; leave such comprehensions alone.
            if isinstance(sub, ast.Lambda):
                arguments = sub.args
                names = {
                    a.arg
                    for a in [
                        *arguments.posonlyargs,
                        *arguments.args,
                        *arguments.kwonlyargs,
                        *([arguments.vararg] if arguments.vararg else []),
                        *([arguments.kwarg] if arguments.kwarg else []),
                    ]
                }
                if names & targets:
                    return None
            elif isinstance(sub, _COMPREHENSIONS):
                for nested in sub.generators:
                    if _stored([nested.target]) & targets:
                        return None
            elif isinstance(sub, ast.NamedExpr):
                return None

    renames = {name: f"{_P}c{number}_{name}" for name in targets}

    class Rename(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.Name:
            if node.id in renames:
                return ast.copy_location(
                    ast.Name(id=renames[node.id], ctx=node.ctx), node
                )
            return node

    def renamed(node: ast.AST) -> Any:
        return Rename().visit(copy.deepcopy(node))

    collected = f"{_P}comp{number}"
    acc = ast.Name(id=collected, ctx=ast.Load())
    if isinstance(value, ast.DictComp):
        empty: ast.expr = ast.Dict(keys=[], values=[])
        write: ast.stmt = ast.Assign(
            targets=[
                ast.Subscript(value=acc, slice=renamed(value.key), ctx=ast.Store())
            ],
            value=renamed(value.value),
        )
    else:
        method = "append" if isinstance(value, ast.ListComp) else "add"
        empty = (
            ast.List(elts=[], ctx=ast.Load())
            if isinstance(value, ast.ListComp)
            else ast.Call(func=ast.Name(id="set", ctx=ast.Load()), args=[], keywords=[])
        )
        write = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(value=acc, attr=method, ctx=ast.Load()),
                args=[renamed(value.elt)],
                keywords=[],
            )
        )
    inner_body: List[ast.stmt] = [write]
    for condition in reversed(generator.ifs):
        inner_body = [ast.If(test=renamed(condition), body=inner_body, orelse=[])]

    loop = ast.For(
        target=renamed(generator.target),
        iter=generator.iter,
        body=inner_body,
        orelse=[],
    )
    result = copy.copy(stmt)
    result.value = ast.Name(id=collected, ctx=ast.Load())  # type: ignore[attr-defined]
    statements: List[ast.stmt] = [
        ast.Assign(targets=[ast.Name(id=collected, ctx=ast.Store())], value=empty),
        loop,
        result,
    ]
    for statement in statements:
        ast.copy_location(statement, stmt)
        ast.fix_missing_locations(statement)
    return statements


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _is_cheap_expression(node: ast.expr) -> bool:
    """True when evaluating ``node`` does no work that belongs on a cluster."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            function = sub.func
            if isinstance(function, ast.Name) and function.id in _CHEAP_BUILTINS:
                continue
            if isinstance(function, ast.Attribute) and function.attr in _CHEAP_METHODS:
                continue
            return False
        if isinstance(sub, (*_COMPREHENSIONS, ast.Lambda, ast.Await, ast.Yield)):
            return False
    return True


def _is_cheap_block(statements: Sequence[ast.stmt]) -> bool:
    """True for a block of plain assignments and returns of cheap expressions."""
    for stmt in statements:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # a docstring
        if isinstance(stmt, ast.Return):
            if stmt.value is not None and not _is_cheap_expression(stmt.value):
                return False
            continue
        if isinstance(stmt, ast.Assign) and all(
            isinstance(target, ast.Name) for target in stmt.targets
        ):
            if not _is_cheap_expression(stmt.value):
                return False
            continue
        return False
    return True


def _function_source(func: Callable) -> ast.FunctionDef:
    lines, first_line = inspect.getsourcelines(func)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    # Report lines as the user's editor numbers them, not relative to the def.
    ast.increment_lineno(tree, first_line - 1)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func.__name__:
            return node
    raise ValueError("the source does not contain a plain function definition")


def plan(func: Callable) -> ParallelPlan:
    """Analyse ``func`` and, if one of its loops can be split, prepare the split.

    Never raises for a function it cannot handle: the returned plan says why.
    """
    func = inspect.unwrap(func)
    name = getattr(func, "__name__", repr(func))
    result = ParallelPlan(func_name=name)

    if not inspect.isfunction(func):
        result.unavailable = "not a Python function"
        return result
    if inspect.isgeneratorfunction(func) or inspect.iscoroutinefunction(func):
        result.unavailable = "generators and coroutines are not split"
        return result
    if inspect.isasyncgenfunction(func):
        result.unavailable = "generators and coroutines are not split"
        return result
    try:
        function = _function_source(func)
    except (OSError, TypeError, SyntaxError, ValueError, IndentationError) as error:
        result.unavailable = f"its source is not available ({error})"
        return result

    arguments = function.args
    parameters = [
        argument.arg
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *([arguments.vararg] if arguments.vararg else []),
            *arguments.kwonlyargs,
            *([arguments.kwarg] if arguments.kwarg else []),
        ]
    ]

    declared_outer: Set[str] = set()
    for stmt in _statements(function.body):
        if isinstance(stmt, (ast.Global, ast.Nonlocal)):
            declared_outer.update(stmt.names)

    # Comprehension statements become explicit loops so one analysis serves both.
    statements: List[ast.stmt] = []
    for number, stmt in enumerate(function.body):
        rewritten = _comprehension_as_loop(stmt, number)
        statements.extend(rewritten if rewritten is not None else [stmt])

    def resolve(identifier: str) -> Tuple[bool, Any]:
        """What a free name in the function refers to right now, if anything."""
        if func.__closure__ and identifier in func.__code__.co_freevars:
            cell = func.__closure__[func.__code__.co_freevars.index(identifier)]
            try:
                return True, cell.cell_contents
            except ValueError:
                return False, None
        if identifier in func.__globals__:
            return True, func.__globals__[identifier]
        return False, None

    for index, stmt in enumerate(statements):
        if not isinstance(stmt, ast.For):
            continue
        before, after = statements[:index], statements[index + 1 :]
        analysis, sites = _analyse_loop(
            stmt, before, after, set(parameters), declared_outer, resolve
        )
        result.loops.append(analysis)
        if result.chosen is not None or not analysis.parallelizable:
            continue
        defined_before = set(parameters) | _stored(before)
        module, body_names, post_names = _generate(
            function, stmt, before, after, analysis, sites, parameters, defined_before
        )
        result.code = compile(module, f"<clustrix split of {name}>", "exec")
        result.chosen = analysis
        result.body_names = body_names
        result.post_names = post_names
        result.pre_is_cheap = _is_cheap_block(before) and _is_cheap_expression(
            stmt.iter
        )
        result.post_is_cheap = _is_cheap_block(after)
    return result


def explain(func: Callable) -> str:
    """Say whether ``func`` would be split across jobs, and why or why not."""
    return plan(func).explain()


# --------------------------------------------------------------------------
# Running a plan
# --------------------------------------------------------------------------


@dataclass
class SplitFunctions:
    """The callables a plan compiles to, bound to one function.

    ``rest`` is the original loop and the code after it, unsplit: what runs
    when a split turns out not to be safe after ``pre`` has already run.
    """

    pre: Callable[[Dict[str, Any]], tuple]
    body: Callable[[Dict[str, Any], Sequence[Any]], tuple]
    post: Callable[[Dict[str, Any], List[tuple]], Any]
    rest: Callable[[Dict[str, Any], Sequence[Any]], Any]


def bind(parallel_plan: ParallelPlan, func: Callable) -> SplitFunctions:
    """Compile the plan against ``func``'s globals and closure as they are now.

    Done per call rather than once: a global the function reads may have been
    rebound since the plan was made, and the split must see what a direct
    call would see.
    """
    if parallel_plan.code is None:
        raise ValueError(f"{parallel_plan.func_name} has no loop to split")
    func = inspect.unwrap(func)
    namespace: Dict[str, Any] = dict(func.__globals__)
    if func.__closure__:
        for cell_name, cell in zip(func.__code__.co_freevars, func.__closure__):
            try:
                namespace[cell_name] = cell.cell_contents
            except ValueError:  # an empty cell: the name is not bound yet
                pass
    # Not an importable module name, so serializers ship these by value.
    namespace["__name__"] = "__clustrix_split__"
    namespace.setdefault("__builtins__", builtins)
    exec(parallel_plan.code, namespace)
    return SplitFunctions(
        pre=namespace[f"{_P}pre"],
        body=namespace[f"{_P}body"],
        post=namespace[f"{_P}post"],
        rest=namespace[f"{_P}rest"],
    )


def bound_arguments(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """``args``/``kwargs`` as the function's own parameter names would see them."""
    bound = inspect.signature(inspect.unwrap(func)).bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def chunk(items: Sequence[Any], count: int) -> List[Sequence[Any]]:
    """Split ``items`` into at most ``count`` contiguous, near-equal runs."""
    total = len(items)
    count = max(1, min(count, total))
    size, extra = divmod(total, count)
    chunks, start = [], 0
    for index in range(count):
        stop = start + size + (1 if index < extra else 0)
        chunks.append(items[start:stop])
        start = stop
    return chunks


def will_be_abandoned(chunk_results: Sequence[tuple]) -> bool:
    """Whether :func:`run` will discard these chunk results and run the loop
    unsplit: some chunk reported that it changed something iterations share.

    For a backend that holds something back until the outcome is known --
    what the chunks printed, say, which the unsplit loop will print again.
    """
    return any(changed for _log, changed, _unchecked in chunk_results)


#: ``invoke(function, *arguments)``: run one of the split's functions
#: somewhere and return its result.
Invoke = Callable[..., Any]


def _here(function: Callable, *arguments: Any) -> Any:
    return function(*arguments)


def run(
    parallel_plan: ParallelPlan,
    func: Callable,
    args: tuple,
    kwargs: dict,
    map_chunks: Callable[[Callable, Dict[str, Any], List[Sequence[Any]]], List[tuple]],
    jobs: int,
    invoke: Invoke = _here,
    on_fallback: Optional[Callable[[str], None]] = None,
) -> Any:
    """Execute ``func`` through its split and return what it would have.

    ``map_chunks(body, env, chunks)`` is the backend: it must call
    ``body(env, chunk)`` for every chunk, each on its **own copy** of
    ``env`` and in a **separate process** from this one, and return the
    results in chunk order. Both conditions are what make an abandoned split
    harmless: nothing a chunk did -- to the environment, or to this
    process's random generators -- is visible when the original loop runs.

    ``invoke`` runs the code around the loop. It is used for ``pre`` unless
    the plan found that code cheap enough to run here, for ``post`` likewise,
    and for the unsplit remainder if the split has to be abandoned.

    The split is abandoned -- after ``pre``, before anything else the caller
    could observe -- when a check that needs real values fails: a value the
    loop only reads turns out to be the object it collects into, or a chunk
    reports that it changed something its iterations share. The original
    loop then runs once, in order, on the environment ``pre`` produced, so
    ``pre`` is never executed twice and the result is the sequential one.
    ``on_fallback`` is told why.
    """
    split = bind(parallel_plan, func)
    arguments = bound_arguments(func, args, kwargs)
    around_pre = _here if parallel_plan.pre_is_cheap else invoke
    around_post = _here if parallel_plan.post_is_cheap else invoke

    outcome = around_pre(split.pre, arguments)
    if outcome[0] == "return":
        return outcome[1]
    _, env, items, aliases = outcome

    def unsplit(reason: str) -> Any:
        if on_fallback is not None:
            on_fallback(f"{parallel_plan.func_name}: not split -- {reason}")
        return invoke(split.rest, env, items)

    if aliases:
        reader, collected = aliases[0]
        return unsplit(
            f"{reader} can reach the same object as {collected}, so an "
            "iteration can see what earlier ones collected"
        )

    body_env = {name: env[name] for name in parallel_plan.body_names if name in env}
    post_env = {name: env[name] for name in parallel_plan.post_names if name in env}

    log: List[tuple] = []
    if len(items):
        modified: Set[str] = set()
        for chunk_log, changed, _unchecked in map_chunks(
            split.body, body_env, chunk(items, jobs)
        ):
            log.extend(chunk_log)
            modified.update(changed)
        if modified:
            return unsplit(
                f"the loop changed {', '.join(sorted(modified))}, which its "
                "iterations share"
            )

    return around_post(split.post, post_env, log)
