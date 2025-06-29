from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="clustrix",
    version="0.1.1",
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
        "Programming Language :: Python :: 3.12",
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
        "requests>=2.25.0",  # For Lambda Cloud and general HTTP requests
        "huggingface_hub>=0.16.0",  # For HuggingFace Spaces integration
    ],
    extras_require={
        "widget": [
            "ipywidgets>=7.6.0",
            "jupyter>=1.0",
            "ipython>=7.0",
        ],
        "kubernetes": ["kubernetes>=20.13.0"],
        "aws": [
            "boto3>=1.26.0",
            "kubernetes>=20.13.0",
        ],
        "azure": [
            "azure-identity>=1.12.0",
            "azure-mgmt-containerservice>=20.0.0",
            "kubernetes>=20.13.0",
        ],
        "gcp": [
            "google-cloud-container>=2.15.0",
            "google-auth>=2.15.0",
            "kubernetes>=20.13.0",
        ],
        "cloud": [
            "boto3>=1.26.0",
            "azure-identity>=1.12.0",
            "azure-mgmt-containerservice>=20.0.0",
            "google-cloud-container>=2.15.0",
            "google-auth>=2.15.0",
            "kubernetes>=20.13.0",
        ],
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
            # Cloud provider dependencies for comprehensive testing
            "boto3>=1.26.0",  # AWS
            "azure-identity>=1.12.0",  # Azure auth
            "azure-mgmt-compute>=30.0.0",  # Azure compute
            "azure-mgmt-containerservice>=20.0.0",  # Azure AKS
            "azure-mgmt-resource>=23.0.0",  # Azure resources
            "azure-mgmt-network>=25.0.0",  # Azure networking
            "google-cloud-compute>=1.11.0",  # GCP compute
            "google-cloud-container>=2.15.0",  # GCP GKE
            "google-auth>=2.15.0",  # GCP auth
            "kubernetes>=20.13.0",  # Kubernetes client
        ],
        "docs": [
            "sphinx>=4.0",
            "sphinx-wagtail-theme>=6.0.0",
            "sphinx-autodoc-typehints>=1.12",
            "nbsphinx>=0.8",
            "jupyter>=1.0",
            "ipython>=7.0",
        ],
        "all": [
            # Widget dependencies
            "ipywidgets>=7.6.0",
            "jupyter>=1.0",
            "ipython>=7.0",
            # Cloud provider dependencies
            "kubernetes>=20.13.0",
            "boto3>=1.26.0",
            "azure-identity>=1.12.0",
            "azure-mgmt-containerservice>=20.0.0",
            "google-cloud-container>=2.15.0",
            "google-auth>=2.15.0",
            # Development dependencies
            "pytest>=6.0",
            "pytest-cov>=2.0",
            "black>=21.0",
            "flake8>=3.8",
            "mypy>=0.812",
            # Documentation dependencies
            "sphinx>=4.0",
            "sphinx-wagtail-theme>=6.0.0",
            "sphinx-autodoc-typehints>=1.12",
            "nbsphinx>=0.8",
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
