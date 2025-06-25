from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="clustrix",
    version="0.1.0",
    author="Contextual Dynamics Laboratory",
    author_email="contextualdynamics@gmail.com",
    description="Seamless distributed computing for Python functions",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ContextLab/clustrix",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Distributed Computing",
    ],
    python_requires=">=3.8",
    install_requires=[
        "paramiko>=2.7.0",
        "pyyaml>=5.4.0",
        "cloudpickle>=2.0.0",
        "dill>=0.3.4",
        "click>=8.0.0",
    ],
    extras_require={
        "kubernetes": ["kubernetes>=20.13.0"],
        "dev": [
            "pytest>=6.0",
            "pytest-cov>=2.0",
            "black>=21.0",
            "flake8>=3.8",
            "mypy>=0.812",
        ],
        "test": [
            "pytest>=6.0",
            "pytest-cov>=2.0",
            "coverage>=6.0",
            "pytest-xdist>=2.0",  # For parallel test execution
            "pytest-mock>=3.0",  # For better mocking support
        ],
        "docs": [
            "sphinx>=4.0",
            "groundwork-sphinx-theme>=1.1.1",
            "sphinx-autodoc-typehints>=1.12",
            "nbsphinx>=0.8",
            "jupyter>=1.0",
            "ipython>=7.0",
        ],
        "all": [
            "kubernetes>=20.13.0",
            "pytest>=6.0",
            "pytest-cov>=2.0",
            "black>=21.0",
            "flake8>=3.8",
            "mypy>=0.812",
            "sphinx>=4.0",
            "groundwork-sphinx-theme>=1.1.1",
            "sphinx-autodoc-typehints>=1.12",
            "nbsphinx>=0.8",
            "jupyter>=1.0",
            "ipython>=7.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "clustrix=clustrix.cli:cli",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
