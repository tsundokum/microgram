from setuptools import setup
  
setup(
    name='microgram',
    version='0.1.0',
    description='Simplest possible Python wrapper for Telegram Bot API',
    readme = "README.md",
    packages=['microgram'],
    # requires-python = ">=3.8",
    install_requires=
        'httpx python-json-logger lark'.split(),
    classifiers = [
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)

