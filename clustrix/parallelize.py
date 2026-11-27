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
assigned, a change to something the function did not create, any direct use
of the process-wide random generators -- is a *blocker*, reported with its
line and reason by :meth:`ParallelPlan.explain`, and the function runs
unsplit.

Split, the three pieces are three functions, and the loop runs in other
processes. Some functions only mean what they do as one piece, and are not
analysed at all: one that declares a name ``global`` or ``nonlocal``, one
wrapped by another decorator (the wrapper's code would be skipped), one
whose compiled code is not what its source compiles to on its own (the file
was edited after import; it uses ``super()`` or a class-private name). A
function defined before the loop that reads a variable assigned in or after
the loop is a blocker for the same reason: it would go on seeing the value
from before.

What cannot be proved from source
---------------------------------

Static analysis of Python can show that a loop is unsafe to split; it can
never show that one is safe, because it cannot see what a called function
does. So the static rule is only the first gate. These checks, which need
real values, run behind it:

* **Aliasing.** After ``pre``, everything the body reads -- the values, the
  items, and the iterable itself before it is listed -- is searched for any
  object reachable from what the loop collects into. ``seen = out`` followed
  by ``out.append(f(seen, x))`` passes the static rule and is caught here,
  before a single chunk is sent anywhere. The search goes through
  containers, attributes, closures, classes and object arrays; if there is
  more to search than it will visit, that counts as found.
* **Mutation.** The body fingerprints every value it reads, and the items it
  was given, before and after its chunk. A difference means an iteration
  changed something its neighbours share.
* **Hidden randomness.** The state of the process-wide generators of
  ``random`` and NumPy is compared around each chunk, which catches a helper
  that draws from them.
* **Identity.** The log does not copy an object that existed before the
  loop: it names it by where it lives (a variable or item, then indexes and
  attributes), and ``post`` puts the original back. So ``out.append(row)``
  collects ``row`` itself, and what the code after the loop does to ``row``
  shows in ``out`` as it would have. A function created in the loop body
  that closes over the loop's variables and is kept is refused here too:
  all such functions share one variable in a sequential run.

When a check fails the split is abandoned and the original loop runs once,
in order, on the environment ``pre`` already produced -- ``pre`` itself is
never run twice -- so the answer is the sequential one either way. If an
iteration raises and the caller could see what the loop had collected by
then (it collects into an argument), the loop is likewise run in order
where the caller's objects are, so they are left as a direct call would
have left them.

What no check can see: state that lives outside the values the loop was
handed. A callee that updates a module-level cache, a library's internal
counter, a file, a database or a GPU's random state passes all of the above;
so does process-wide configuration set before the loop (the ``decimal``
context, the working directory, an environment variable changed in the
function), which a worker process does not have; so does an object whose
pickle leaves its state out, since pickling is how a change is noticed.
Identity is kept for objects found through lists, tuples, dict values and
attributes, not for the elements of a set or the keys of a dict. Side
effects of that kind still happen once per iteration, but in an unspecified
order across chunks. That is the residual assumption of splitting a loop,
and it is the user's to accept.

The generated functions reference nothing from clustrix, so a worker that
does not have clustrix installed can run them.
"""

import __future__
import ast
import builtins
import copy
import dis
import functools
import inspect
import logging
import re
import textwrap
import time
import tokenize
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

#: What a name the function does not define may be when the loop calls a
#: method on it or assigns through it: code. ``np.save(...)`` and
#: ``logger.info(...)`` are calls *into* something. Anything else is data
#: the function did not create, and a worker's change to it would be lost.
_CODE_TYPES = (
    types.ModuleType,
    types.FunctionType,
    types.BuiltinFunctionType,
    type,
    logging.Logger,
    logging.LoggerAdapter,
)

#: How many objects a run-time search will visit before it gives up and the
#: loop runs in order.
_WALK_LIMIT = 5_000_000


class CannotShip(Exception):
    """A value the split needs cannot be copied to another process.

    Raised by a ``map_chunks`` backend; :func:`run` then runs the loop in
    order where the values are.
    """


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
    #: Names from outside the function that the loop calls methods on, which
    #: were code when the plan was made. Checked again on every run: the
    #: plan may be reused for another closure of the same source.
    assumed_code: Tuple[str, ...] = ()

    @property
    def parallelizable(self) -> bool:
        return self.chosen is not None

    def explain(self) -> str:
        """A plain-text account of the decision, for the person who asked."""
        lines = [f"{self.func_name}:"]
        if self.unavailable:
            lines.append(f"  not analysed: {self.unavailable}")
            return _as_written("\n".join(lines))
        if not self.loops:
            lines.append("  no loop at the top level of the function to split")
            return _as_written("\n".join(lines))
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
        return _as_written("\n".join(lines))


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
                # Deleting a name needs it to be bound, as reading it does.
                if isinstance(target, ast.Name) and target.id in self.tracked:
                    if target.id not in assigned:
                        self.violations.setdefault(target.id, node.lineno)
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


def _global_rng_blockers(body: Sequence[ast.stmt]) -> List[Blocker]:
    """Any direct use of a process-wide random generator, seeding included.

    Seeding at the top of each iteration does make the draws that follow
    depend on the iteration alone, but the generator is still one object
    shared by the whole process: what it holds after the loop -- which the
    code after the loop, and the caller, can draw from -- is the state the
    *last* iteration left, and a split leaves it untouched instead.
    """
    calls = _global_rng_calls(body)
    if not calls:
        return []
    line, _, name, _ = calls[0]
    return [
        Blocker(
            line,
            f"{name}() uses a random generator shared by the whole process, "
            "so what it returns and what it leaves behind depend on the order "
            "of the iterations; create one per iteration instead, e.g. "
            "rng = np.random.default_rng(seed + i)",
        )
    ]


def _parameter_names(arguments: ast.arguments) -> List[str]:
    return [
        argument.arg
        for argument in [
            *arguments.posonlyargs,
            *arguments.args,
            *([arguments.vararg] if arguments.vararg else []),
            *arguments.kwonlyargs,
            *([arguments.kwarg] if arguments.kwarg else []),
        ]
    ]


def _free_reads(scope: ast.AST) -> Set[str]:
    """Names a nested function, lambda or class reads from the scope around it.

    Those are the variables it *closes over*: it sees their current value
    whenever it is called, not the value they had when it was defined.
    """
    own: Set[str] = set()
    if isinstance(scope, ast.Lambda):
        own.update(_parameter_names(scope.args))
        roots: List[ast.AST] = [scope.body]
    elif isinstance(scope, ast.ClassDef):
        # A class body's own names do not hide anything from its methods.
        roots = list(scope.body)
    else:
        assert isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        own.update(_parameter_names(scope.args))
        own.update(_stored(scope.body))
        roots = list(scope.body)
    reads: Set[str] = set()
    stack = roots
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED_SCOPES):
            reads |= _free_reads(node)
            continue
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                reads.add(node.id)
            elif not isinstance(scope, ast.ClassDef):
                own.add(node.id)  # a comprehension's variable
        stack.extend(ast.iter_child_nodes(node))
    return reads - own


def _nested_scopes(nodes: Sequence[ast.AST]) -> List[ast.AST]:
    """The outermost functions, lambdas and classes defined under ``nodes``."""
    found: List[ast.AST] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, _NESTED_SCOPES):
            found.append(node)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _resolve(func: Callable, identifier: str) -> Tuple[bool, Any]:
    """What a free name in ``func`` refers to right now, if anything."""
    code = func.__code__
    if func.__closure__ and identifier in code.co_freevars:
        cell = func.__closure__[code.co_freevars.index(identifier)]
        try:
            return True, cell.cell_contents
        except ValueError:
            return False, None
    if identifier in func.__globals__:
        return True, func.__globals__[identifier]
    if hasattr(builtins, identifier):
        return True, getattr(builtins, identifier)
    return False, None


def _is_code(func: Callable, identifier: str) -> bool:
    found, value = _resolve(func, identifier)
    return found and isinstance(value, _CODE_TYPES)


def _as_written(text: str) -> str:
    """Undo the renaming a comprehension's variables get when it is rewritten
    as a loop, so a report names things the way the user's source does."""
    text = re.sub(rf"{_P}c\d+_(\w+)", r"\1", text)
    return re.sub(rf"{_P}comp\d+", "the comprehension's result", text)


def _analyse_loop(
    loop: ast.For,
    before: Sequence[ast.stmt],
    after: Sequence[ast.stmt],
    parameters: Set[str],
    is_code: Callable[[str], bool],
) -> Tuple[LoopAnalysis, List[_Site], Set[str]]:
    header = _as_written(f"for {ast.unparse(loop.target)} in {ast.unparse(loop.iter)}")
    analysis = LoopAnalysis(line=loop.lineno, header=header)
    block = analysis.blockers
    body = loop.body

    if loop.orelse:
        block.append(Blocker(loop.orelse[0].lineno, "the loop has an else clause"))
    if not _is_plain_target(loop.target):
        block.append(
            Blocker(
                loop.lineno,
                f"the loop assigns each item to {ast.unparse(loop.target)}, which "
                "is not a plain variable, so every iteration writes to the "
                "same place",
            )
        )

    # -- statements that end or escape the loop ------------------------------
    for stmt in _statements(body):
        if isinstance(stmt, ast.Return):
            block.append(Blocker(stmt.lineno, "the loop body returns"))
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
    # What the code after the loop reads *before assigning it*. Merely
    # being read there is not enough: the variable of a second loop is
    # assigned first, so it is not the value this loop left behind.
    liveness = _ReadBeforeAssigned(assigned_in_body)
    liveness.block(after, set())
    used_after = set(liveness.violations)
    read_in_header = _loaded([loop.iter])

    # A function defined before the loop keeps reading the variables of the
    # scope it was defined in. Split, the loop and the code after it run in
    # scopes of their own, so anything they assign would be invisible to it.
    assigned_later = assigned_in_body | _stored(after)
    for scope in _nested_scopes(before):
        for name in sorted(_free_reads(scope) & assigned_later):
            block.append(
                Blocker(
                    getattr(scope, "lineno", loop.lineno),
                    f"a function defined before the loop reads {name}, which "
                    "is assigned again in or after the loop; it would go on "
                    "seeing the old value",
                )
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

    assumed_code: Set[str] = set()
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
            if name in assigned_in_body and name not in used_after:
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
            if is_code(name):
                assumed_code.add(name)
            else:
                block.append(
                    Blocker(
                        line,
                        f"{name} is not created in this function and the loop "
                        "changes it; a change made in a worker would be lost. "
                        "Create it inside the function and return it",
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
        elif name in used_after:
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
    return analysis, sites, assumed_code


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


def {_P}reach({_P}roots, {_P}through_code, {_P}seen):
    # Every object reachable from the roots, once. Numbers, strings and
    # modules are neither yielded nor entered. Raises OverflowError rather
    # than stop silently: an unfinished search has proved nothing.
    import gc
    import sys
    import types
    atomic = (int, float, complex, str, bytes, bool, type(None), range,
              types.ModuleType)
    numpy = sys.modules.get("numpy")
    stack = list({_P}roots)
    while stack:
        obj = stack.pop()
        if type(obj) in atomic or id(obj) in {_P}seen:
            continue
        {_P}seen.add(id(obj))
        if len({_P}seen) > {_WALK_LIMIT}:
            raise OverflowError
        yield obj
        if isinstance(obj, type):
            # A class written in Python: its attributes are values too.
            if {_P}through_code and obj.__flags__ & 512:
                stack.extend(vars(obj).values())
                stack.extend(obj.__bases__)
        elif isinstance(obj, types.FunctionType):
            if {_P}through_code:
                # Not its globals: that is the whole module.
                for cell in obj.__closure__ or ():
                    try:
                        stack.append(cell.cell_contents)
                    except ValueError:
                        pass
                stack.extend(obj.__defaults__ or ())
                stack.extend((obj.__kwdefaults__ or {{}}).values())
        elif isinstance(obj, (types.MethodType, types.BuiltinFunctionType,
                              types.MethodWrapperType)):
            # ``out.__len__`` holds ``out``.
            stack.append(obj.__self__)
            if isinstance(obj, types.MethodType):
                stack.append(obj.__func__)
        elif numpy is not None and isinstance(obj, numpy.ndarray):
            # The garbage collector does not look inside an array.
            if obj.dtype.hasobject:
                stack.extend(obj.flat)
            if obj.base is not None:
                stack.append(obj.base)
        else:
            stack.extend(gc.get_referents(obj))


def {_P}aliases({_P}env, {_P}written, {_P}read, {_P}items=None):
    # Pairs (what the body reads, what the loop collects into) where the
    # first can reach an object the second can also reach. The body does not
    # perform its writes, so anything it could read them back through would
    # show it stale data.
    import types
    never_written = (tuple, frozenset, type, types.FunctionType,
                     types.BuiltinFunctionType, types.MethodType,
                     types.MethodWrapperType)
    targets = {{}}
    try:
        for name in {_P}written:
            if name in {_P}env:
                for obj in {_P}reach([{_P}env[name]], False, set()):
                    if not isinstance(obj, never_written):
                        targets.setdefault(id(obj), name)
        if not targets:
            return []
        readers = [(name, {_P}env[name]) for name in {_P}read if name in {_P}env]
        if {_P}items is not None:
            readers.append(({_ITEMS!r}, {_P}items))
        found = []
        seen = set()
        for name, value in readers:
            for obj in {_P}reach([value], True, seen):
                if id(obj) in targets:
                    found.append((name, targets[id(obj)]))
                    break
        return found
    except OverflowError:
        return [(None, sorted({_P}written)[0])]


def {_P}index({_P}env, {_P}items):
    # Where each object the chunk was handed lives: {{id: (path, object)}}.
    # A path names a variable or an item, then steps into lists, tuples,
    # dict values and attributes. It is how an object that existed before
    # the loop is named in the log instead of being copied into it.
    import types
    atomic = (int, float, complex, str, bytes, bool, type(None), range)
    opaque = (type, types.ModuleType, types.FunctionType)
    table = {{}}
    stack = []
    if type({_P}items) is not range:
        stack.extend(
            ((("i", position),), item)
            for position, item in enumerate({_P}items)
        )
    stack.extend(((("e", name),), {_P}env[name]) for name in sorted({_P}env))
    while stack and len(table) < {_WALK_LIMIT}:
        path, obj = stack.pop()
        if type(obj) in atomic or id(obj) in table:
            continue
        table[id(obj)] = (path, obj)
        if isinstance(obj, list):
            stack.extend(
                (path + (("s", k),), e) for k, e in enumerate(list.__iter__(obj))
            )
        elif isinstance(obj, tuple):
            stack.extend(
                (path + (("s", k),), e) for k, e in enumerate(tuple.__iter__(obj))
            )
        elif isinstance(obj, dict):
            stack.extend(
                (path + (("v", k),), e) for k, e in enumerate(dict.values(obj))
            )
        if not isinstance(obj, opaque):
            attributes = getattr(obj, "__dict__", None)
            if type(attributes) is dict:
                stack.extend(
                    (path + (("a", name),), e)
                    for name, e in attributes.items()
                    if type(name) is str
                )
    return table


def {_P}dump({_P}log, {_P}table):
    # The log as bytes, a pre-existing object written as its path. Returns
    # (bytes, the variables those paths start from, what went wrong).
    import io
    import pickle
    import types
    try:
        from cloudpickle import Pickler as base
    except ImportError:
        base = pickle.Pickler
    here = {_P}dump.__code__.co_filename
    table = {_P}table  # a name starting __ is rewritten inside a class
    used = set()
    escaped = []

    class Tokens(base):
        def persistent_id(self, obj):
            entry = table.get(id(obj))
            if entry is not None and entry[1] is obj:
                path = entry[0]
                used.add(path[0][1] if path[0][0] == "e" else {_ITEMS!r})
                return path
            if (
                type(obj) is types.FunctionType
                and obj.__closure__
                and obj.__code__.co_filename == here
            ):
                escaped.append(obj.__name__)
            return None

    buffer = io.BytesIO()
    try:
        Tokens(buffer, protocol=4).dump({_P}log)
    except Exception as error:
        return None, [], (
            "a value it collects, which cannot be copied between processes "
            "(%s: %s)" % (type(error).__name__, error)
        )
    if escaped:
        return None, [], (
            "the variables of a function defined in the loop body (%s), "
            "which is kept after its iteration" % escaped[0]
        )
    return buffer.getvalue(), sorted(used), None


def {_P}load({_P}blobs, {_P}env, {_P}items):
    # The chunks' logs as one list, each path replaced by the object it
    # names *here*.
    import io
    import pickle
    values = {{}}

    def resolve(path, start):
        if path[0][0] == "e":
            obj = {_P}env[path[0][1]]
        else:
            obj = {_P}items[start + path[0][1]]
        for kind, key in path[1:]:
            if kind == "s":
                obj = (list if isinstance(obj, list) else tuple).__getitem__(obj, key)
            elif kind == "v":
                if id(obj) not in values:
                    values[id(obj)] = (obj, list(dict.values(obj)))
                obj = values[id(obj)][1][key]
            else:
                obj = obj.__dict__[key]
        return obj

    class Tokens(pickle.Unpickler):
        start = 0

        def persistent_load(self, path):
            return resolve(path, self.start)

    log = []
    for blob, start in {_P}blobs:
        reader = Tokens(io.BytesIO(blob))
        reader.start = start
        log.extend(reader.load())
    return log
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
    body_names = tuple(sorted((_loaded(loop.body) & defined_before) - accumulators))
    post_names = tuple(sorted(((_loaded(after) & defined_before) | accumulators)))
    env_names = tuple(sorted(set(body_names) | set(post_names)))

    # -- pre ------------------------------------------------------------------
    pre_body: List[ast.stmt] = []
    for name in parameters:
        pre_body.extend(_parse_statements(f"{name} = {_P}args[{name!r}]"))
    transformer = _ReturnsAsTuples()
    pre_body.extend(transformer.visit(copy.deepcopy(stmt)) for stmt in before)
    written = tuple(sorted(accumulators))
    pre_body.extend(_parse_statements(f"""
            {_P}items = {ast.unparse(loop.iter)}
            {_P}scope = locals()
            {_P}found = {{{_P}n: {_P}scope[{_P}n] for {_P}n in {env_names!r} if {_P}n in {_P}scope}}
            # Searched before the items are listed: a generator may be
            # reading what the loop collects, and listing it would use it up.
            {_P}shared = {_P}aliases({_P}found, {written!r}, {body_names!r}, {_P}items)
            if not {_P}shared and not isinstance({_P}items, range):
                {_P}items = list({_P}items)
                {_P}shared = {_P}aliases({_P}found, {written!r}, (), {_P}items)
            if {_P}shared and {_P}inline:
                return ("fallback", {_P}shared, {_P}rest({_P}found, {_P}items))
            return ("env", {_P}found, {_P}items, {_P}shared)
            """))
    pre = _function(f"{_P}pre", [f"{_P}args", f"{_P}inline=False"], pre_body)

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
            {_P}rng_before = {_P}rng_state()
            {_P}table = {_P}index({_P}env, {_P}items)
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
            {_P}changed.extend(
                n + ", which could not be checked for changes"
                for n in sorted({_P}before) if {_P}before[n] is None
            )
            if {_P}rng_state() != {_P}rng_before:
                {_P}changed.append({_RNG!r})
            {_P}blob, {_P}used, {_P}problem = {_P}dump({_P}log, {_P}table)
            if {_P}problem is not None:
                {_P}changed.append({_P}problem)
            return ({_P}blob, {_P}changed, {_P}used)
            """))
    body = _function(f"{_P}body", [f"{_P}env", f"{_P}items"], body_statements)

    # -- post -----------------------------------------------------------------
    post_statements: List[ast.stmt] = _bind_from_env(post_names)
    post_statements.extend(
        _parse_statements(f"{_P}log = {_P}load({_P}blobs, {_P}env, {_P}items)")
    )
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
    post = _function(
        f"{_P}post", [f"{_P}env", f"{_P}blobs", f"{_P}items"], post_statements
    )

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


def _is_cheap_expression(node: ast.expr, local_names: Set[str]) -> bool:
    """True when evaluating ``node`` does no work that belongs on a cluster,
    and reads nothing that could be different there.

    ``os.environ["HOME"]`` and ``sys.platform`` are cheap and wrong: they
    are facts about the machine they are evaluated on. So an attribute or an
    index is allowed only on a variable of the function itself.
    """
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
        if isinstance(sub, (ast.Attribute, ast.Subscript)):
            root: ast.expr = sub
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id not in local_names:
                return False
    return True


def _is_plain_target(target: ast.expr) -> bool:
    """A name, or a tuple or list of them: binding it runs no code."""
    if isinstance(target, (ast.Tuple, ast.List)):
        return all(_is_plain_target(element) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _is_plain_target(target.value)
    return isinstance(target, ast.Name)


def _is_cheap_block(statements: Sequence[ast.stmt], local_names: Set[str]) -> bool:
    """True for a block of imports and of plain assignments and returns of
    cheap expressions. ``local_names`` are the function's own variables on
    entry to the block; the block's assignments are added as it goes.

    An import counts as cheap although it runs code: a function whose only
    preparation is ``import os`` should not cost a job for it. If the module
    is not installed where the caller is, :func:`run` notices and runs the
    block where the rest of the work runs instead. What it imports is not
    one of the function's own variables, though: ``os.getcwd()`` after it is
    still a question about a machine.
    """
    known = set(local_names)
    for stmt in statements:
        if isinstance(stmt, (ast.Pass, ast.Import, ast.ImportFrom)):
            known -= _stored([stmt])
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # a docstring
        if isinstance(stmt, ast.Return):
            if stmt.value is not None and not _is_cheap_expression(stmt.value, known):
                return False
            continue
        if isinstance(stmt, ast.Assign) and all(
            _is_plain_target(target) for target in stmt.targets
        ):
            if not _is_cheap_expression(stmt.value, known):
                return False
            known |= _stored(stmt.targets)
            continue
        return False
    return True


#: Instructions whose presence depends on how the source was laid out or on
#: how long the code is, not on what it does.
_LAYOUT_ONLY = frozenset({"PUSH_NULL", "PRECALL", "EXTENDED_ARG", "NOP", "CACHE"})
_SAME_INSTRUCTION = {"LOAD_METHOD": "LOAD_ATTR", "CALL_METHOD": "CALL_FUNCTION"}
_JUMPS = frozenset(dis.hasjrel) | frozenset(dis.hasjabs)


def _instructions(code: types.CodeType) -> List[Tuple[str, Any]]:
    """What ``code`` does, as a list that ignores where its source lines fall.

    Bytecode is not a pure function of the syntax tree: the compiler picks
    a shorter sequence for ``obj.method(x)`` when the call sits on one line,
    and a tool that rewrites a module as it is imported (pytest does, for
    ``assert``) moves lines without changing meaning. Jump distances shift
    with either. Everything else is compared.
    """
    result = []
    for instruction in dis.get_instructions(code):
        name = _SAME_INSTRUCTION.get(instruction.opname, instruction.opname)
        if name in _LAYOUT_ONLY:
            continue
        value: Any = instruction.argval
        if instruction.opcode in _JUMPS or isinstance(value, types.CodeType):
            value = None
        elif not isinstance(value, (str, int, float, bytes, type(None), tuple)):
            value = repr(value)
        result.append((name, (type(value).__name__, value)))
    return result


def _same_code(ours: types.CodeType, theirs: types.CodeType) -> bool:
    """Whether two code objects are the same program, wherever they came from."""
    for attribute in ("co_names", "co_varnames", "co_freevars"):
        if getattr(ours, attribute) != getattr(theirs, attribute):
            return False
    if _instructions(ours) != _instructions(theirs):
        return False
    if len(ours.co_consts) != len(theirs.co_consts):
        return False
    for mine, other in zip(ours.co_consts, theirs.co_consts):
        if isinstance(mine, types.CodeType) and isinstance(other, types.CodeType):
            if not _same_code(mine, other):
                return False
        elif type(mine) is not type(other) or mine != other:
            return False
    return True


def _compiles_to(node: ast.FunctionDef, func: Callable) -> bool:
    """Whether ``node``, compiled on its own, is the code ``func`` runs.

    It is not when the file was edited after it was imported, when the
    source found belongs to some other function of the same name, or when
    the function only means what it does inside its class (``self.__x`` is
    stored under another name there, and ``super()`` needs the class). The
    split is compiled from this source on its own, so in each of those cases
    it would be a different program.
    """
    code = func.__code__
    if "__class__" in code.co_freevars:
        return False  # super(): only meaningful inside its class
    alone = copy.deepcopy(node)
    alone.decorator_list = []
    statements: List[ast.stmt] = [alone]
    if code.co_freevars:
        # Compile it as a closure over the same names, as it was.
        outer = _function(
            f"{_P}outer",
            [],
            [
                *_parse_statements(
                    "\n".join(f"{name} = None" for name in code.co_freevars)
                ),
                alone,
            ],
        )
        statements = [outer]
    module = ast.Module(body=statements, type_ignores=[])
    ast.fix_missing_locations(module)
    futures = 0
    for feature in __future__.all_feature_names:
        futures |= getattr(__future__, feature).compiler_flag
    try:
        compiled = compile(
            module,
            "<clustrix check>",
            "exec",
            flags=code.co_flags & futures,
            dont_inherit=True,
        )
    except (SyntaxError, ValueError):
        return False
    pending = [compiled]
    while pending:
        candidate = pending.pop()
        if candidate.co_name == node.name and candidate is not compiled:
            return _same_code(candidate, code)
        pending.extend(c for c in candidate.co_consts if isinstance(c, types.CodeType))
    return False


def _function_source(func: Callable) -> ast.FunctionDef:
    lines, first_line = inspect.getsourcelines(func)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    # Report lines as the user's editor numbers them, not relative to the def.
    ast.increment_lineno(tree, first_line - 1)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func.__name__:
            # ``inspect`` finds source by file name and line number. For a
            # function compiled from a string under a made-up file name it
            # can come back with some *other* text entirely; splitting a
            # function according to source that is not its own would run
            # the wrong code.
            code = func.__code__
            declared = code.co_varnames[
                : code.co_argcount
                + code.co_kwonlyargcount
                + bool(code.co_flags & inspect.CO_VARARGS)
                + bool(code.co_flags & inspect.CO_VARKEYWORDS)
            ]
            arguments = node.args
            written = [
                argument.arg
                for argument in [
                    *arguments.posonlyargs,
                    *arguments.args,
                    *arguments.kwonlyargs,
                    *([arguments.vararg] if arguments.vararg else []),
                    *([arguments.kwarg] if arguments.kwarg else []),
                ]
            ]
            if list(declared) != written:
                raise ValueError("the source found does not belong to this function")
            if not _compiles_to(node, func):
                raise ValueError(
                    "the source found is not what the function runs: the "
                    "file changed after it was loaded, or the function uses "
                    "super() or a class-private name"
                )
            return node
    raise ValueError("the source does not contain a plain function definition")


def plan(func: Callable) -> ParallelPlan:
    """Analyse ``func`` and, if one of its loops can be split, prepare the split.

    Never raises for a function it cannot handle: the returned plan says why.
    """
    name = getattr(func, "__name__", repr(func))
    result = ParallelPlan(func_name=name)

    if not inspect.isfunction(func):
        result.unavailable = "not a Python function"
        return result
    if hasattr(func, "__wrapped__"):
        # ``inspect`` would hand back the source of the function inside, and
        # running a split of that would skip whatever the wrapper does.
        result.unavailable = (
            "it is wrapped by another decorator, whose own code a split "
            "would skip; put @cluster directly on the function with the loop"
        )
        return result
    if inspect.isgeneratorfunction(func) or inspect.iscoroutinefunction(func):
        result.unavailable = "generators and coroutines are not split"
        return result
    if inspect.isasyncgenfunction(func):
        result.unavailable = "generators and coroutines are not split"
        return result
    try:
        function = _function_source(func)
    except (
        OSError,
        TypeError,
        SyntaxError,
        ValueError,
        IndentationError,
        tokenize.TokenError,
    ) as error:
        result.unavailable = f"its source is not available ({error})"
        return result

    parameters = _parameter_names(function.args)

    for node in ast.walk(function):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            # Split, the function's pieces run in scopes of their own and,
            # for the loop, in other processes; an assignment to a variable
            # that lives outside the function would land in the wrong place.
            result.unavailable = (
                f"line {node.lineno} declares {', '.join(node.names)} "
                f"{'global' if isinstance(node, ast.Global) else 'nonlocal'}; "
                "a function that assigns variables outside itself is not split"
            )
            return result

    # Comprehension statements become explicit loops so one analysis serves both.
    statements: List[ast.stmt] = []
    for number, stmt in enumerate(function.body):
        rewritten = _comprehension_as_loop(stmt, number)
        statements.extend(rewritten if rewritten is not None else [stmt])

    imported = {
        name
        for node in ast.walk(function)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in _stored([node])
    }

    for index, stmt in enumerate(statements):
        if not isinstance(stmt, ast.For):
            continue
        before, after = statements[:index], statements[index + 1 :]
        analysis, sites, assumed_code = _analyse_loop(
            stmt, before, after, set(parameters), functools.partial(_is_code, func)
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
        result.assumed_code = tuple(sorted(assumed_code))
        own = set(parameters) - imported
        result.pre_is_cheap = _is_cheap_block(before, own) and _is_cheap_expression(
            stmt.iter, (own | _stored(before)) - imported
        )
        result.post_is_cheap = _is_cheap_block(
            after, (own | _stored(before) | _stored([stmt])) - imported
        )
    return result


def explain(func: Callable) -> str:
    """Say whether ``func`` would be split across jobs, and why or why not.

    Accepts the function as written or as ``@cluster`` returns it.
    """
    if hasattr(func, "_cluster_config") and hasattr(func, "__wrapped__"):
        func = func.__wrapped__
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

    pre: Callable[..., tuple]
    body: Callable[[Dict[str, Any], Sequence[Any]], tuple]
    post: Callable[[Dict[str, Any], List[tuple], Optional[Sequence[Any]]], Any]
    rest: Callable[[Dict[str, Any], Sequence[Any]], Any]
    #: ``aliases(env, written, read)``: the run-time search for shared objects.
    aliases: Callable[..., List[tuple]]


def bind(parallel_plan: ParallelPlan, func: Callable) -> SplitFunctions:
    """Compile the plan against ``func``'s globals and closure as they are now.

    Done per call rather than once: a global the function reads may have been
    rebound since the plan was made, and the split must see what a direct
    call would see.
    """
    if parallel_plan.code is None:
        raise ValueError(f"{parallel_plan.func_name} has no loop to split")
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
        aliases=namespace[f"{_P}aliases"],
    )


def bound_arguments(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """``args``/``kwargs`` as the function's own parameter names would see them."""
    bound = inspect.signature(func).bind(*args, **kwargs)
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
    return any(changed for _log, changed, _used in chunk_results)


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
    should_split: Optional[Callable[[int], bool]] = None,
    on_unsplit: Optional[Callable[[int, float], None]] = None,
) -> Any:
    """Execute ``func`` through its split and return what it would have.

    ``map_chunks(body, env, chunks)`` is the backend: it must call
    ``body(env, chunk)`` for every chunk, each on its **own copy** of
    ``env`` and in a **separate process** from this one, and return the
    results in chunk order. Both conditions are what make an abandoned split
    harmless: nothing a chunk did -- to the environment, or to this
    process's random generators -- is visible when the original loop runs.
    A backend that cannot copy ``env`` or a chunk raises :class:`CannotShip`,
    and the loop runs in order instead. An exception from an iteration must
    come out of ``map_chunks`` as the one from the earliest failing chunk:
    that is the one a sequential run would have raised.

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

    ``should_split(count)`` lets the caller decline a split that is safe but
    not worth making. It is asked once, with the number of items, after
    ``pre`` and the aliasing check and only when there are items. If it
    answers False the original loop runs through ``invoke``, in order, on
    the environment ``pre`` produced -- the real objects, no copies. That is
    a choice and not a failed check, so ``on_fallback`` hears nothing;
    ``on_unsplit(count, seconds)`` is told how long that run took by the
    wall clock, so the caller can decide differently next time.
    """
    name = parallel_plan.func_name

    def tell(reason: str) -> None:
        if on_fallback is not None:
            on_fallback(f"{name}: not split -- {reason}")

    changed_kind = [
        identifier
        for identifier in parallel_plan.assumed_code
        if not _is_code(func, identifier)
    ]
    if changed_kind:
        # The plan was made when this name was a module, a function or a
        # class. It is data now, and the loop writes to it.
        tell(
            f"{changed_kind[0]} is not created in this function and the "
            "loop changes it; a change made in a worker would be lost"
        )
        return invoke(functools.partial(func, *args, **kwargs))

    split = bind(parallel_plan, func)
    arguments = bound_arguments(func, args, kwargs)
    around_pre = _here if parallel_plan.pre_is_cheap else invoke
    around_post = _here if parallel_plan.post_is_cheap else invoke

    # Wherever ``pre`` runs away from here, it runs the loop itself if the
    # aliasing check fails: the values are there, and may not travel.
    try:
        outcome = around_pre(split.pre, arguments, around_pre is not _here)
    except ImportError:
        if around_pre is invoke:
            raise
        # ``pre`` was run here because it only imports and assigns, and a
        # module it imports is not installed here. It may be where the work
        # runs; an import that fails there too is raised from there.
        outcome = invoke(split.pre, arguments, invoke is not _here)
    if outcome[0] == "return":
        return outcome[1]

    def shared(aliases: List[tuple]) -> str:
        reader, collected = aliases[0]
        if reader is None:
            return (
                "there are too many values to check that none of them is "
                f"what {collected} collects into"
            )
        return (
            f"{reader} can reach an object {collected} collects into, so an "
            "iteration could see what earlier ones collected"
        )

    if outcome[0] == "fallback":
        tell(shared(outcome[1]))
        return outcome[2]
    _, env, items, aliases = outcome

    def unsplit(reason: str) -> Any:
        tell(reason)
        return invoke(split.rest, env, items)

    if aliases:
        return unsplit(shared(aliases))

    if not len(items):
        return around_post(
            split.post,
            {key: env[key] for key in parallel_plan.post_names if key in env},
            [],
            None,
        )

    if should_split is not None and not should_split(len(items)):
        started = time.perf_counter()
        result = invoke(split.rest, env, items)
        if on_unsplit is not None:
            on_unsplit(len(items), time.perf_counter() - started)
        return result

    body_env = {key: env[key] for key in parallel_plan.body_names if key in env}
    chunks = chunk(items, jobs)
    try:
        results = map_chunks(split.body, body_env, chunks)
    except CannotShip as error:
        return unsplit(str(error))
    except Exception:
        collected = tuple(parallel_plan.chosen.accumulators)  # type: ignore[union-attr]
        handed_in = {f"argument {key}": value for key, value in arguments.items()}
        if invoke is _here and split.aliases(
            {**handed_in, **{key: env[key] for key in collected if key in env}},
            collected,
            tuple(handed_in),
        ):
            # The caller can see what the loop collects into. A direct call
            # would have left there everything collected before the error;
            # running the loop in order here does the same and then raises
            # the same error. Not a failed check, so nobody is told.
            return split.rest(env, items)
        raise

    blobs: List[tuple] = []
    modified: Set[str] = set()
    named: Set[str] = set()
    start = 0
    for piece, (blob, changed, used) in zip(chunks, results):
        blobs.append((blob, start))
        start += len(piece)
        modified.update(changed)
        named.update(used)
    if modified:
        return unsplit(
            f"the loop changed {', '.join(sorted(modified))}, which its "
            "iterations share. That was found after the loop had been run "
            "in pieces, so anything it does besides computing (writing a "
            "file, calling a service) happened in that attempt as well"
        )

    # ``post`` gets what it reads, and whatever the log names instead of
    # copying: an object that existed before the loop is put back as itself.
    post_env = {
        key: env[key] for key in set(parallel_plan.post_names) | named if key in env
    }
    return around_post(split.post, post_env, blobs, items if _ITEMS in named else None)
