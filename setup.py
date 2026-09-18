from setuptools import setup, find_packages

setup(
    name="claude-code-replica",
    version="2.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "claude-replica=claude_replica.cli:main",
            "claudereplica=claude_replica.cli:main",
        ],
    },
)
