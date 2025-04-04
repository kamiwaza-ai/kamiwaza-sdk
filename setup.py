#kamiwaza/kamiwaza-sdk/setup.py

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]

# Add CLI dependencies
cli_requirements = [
    'click>=8.0.0',
    'rich>=10.0.0'
]

setup(
    name="kamiwaza",
    version="0.3.3.0",
    author="Kamiwaza Team",
    author_email="tyler@kamiwaza.ai",
    description="Python client library for the Kamiwaza AI Platform",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/kamiwaza/kamiwaza-client",
    packages=find_packages(exclude=["tests*", "examples*"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.10",
    install_requires=requirements + cli_requirements,
    include_package_data=True,
    package_data={
        "kamiwaza_client": ["py.typed"],
    },
    entry_points={
        'console_scripts': [
            'kamiwaza=kamiwaza_client.cli.main:cli'
        ]
    }
) # end



