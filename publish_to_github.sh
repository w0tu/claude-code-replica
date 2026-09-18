#!/bin/bash
# Script to publish the repository to your tqus GitHub profile

echo "Checking GitHub authentication..."
if ! gh auth status >/dev/null 2>&1; then
    echo "Not authenticated. Please authenticate with GitHub CLI first by running:"
    echo "  gh auth login -w -s repo"
    exit 1
fi

echo "Creating remote repository 'tqus/claude-code-replica'..."
output=$(gh repo create tqus/claude-code-replica --public --source=. --remote=origin --push 2>&1)
exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo "Successfully published to https://github.com/tqus/claude-code-replica"
else
    echo "$output"
    if echo "$output" | grep -qi "Resource not accessible by personal access token"; then
        echo ""
        echo "ERROR: Your current GitHub Personal Access Token lacks permission to create repositories."
        echo "To fix this, you must re-authenticate and grant the 'repo' scope."
        echo "Run the following command, follow the prompts, and then try this script again:"
        echo ""
        echo "  gh auth login -w -s repo"
    else
        echo "Failed to create/push to repository. Please check the error above."
    fi
    exit 1
fi
