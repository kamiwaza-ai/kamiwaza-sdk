from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

print("Packages found: ", find_packages())

setup(
    name="kamiwaza-client",
    version="0.1.0",
    author="",
    author_email="",
    description="A client SDK for interacting with the Kamiwaza AI platform",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "requests>=2.25.1",
        "pydantic>=1.8.1",
    ],
)