#!/bin/bash
set -e

echo "Setting up required GitHub labels..."

gh label create "ready-for-review" \
  --description "All CI checks passed - ready for maintainer review" \
  --color "0E8A16" \
  --force || echo "Label 'ready-for-review' may already exist"

gh label create "changes-required" \
  --description "CI checks failed - changes needed" \
  --color "D93F0B" \
  --force || echo "Label 'changes-required' may already exist"

gh label create "auto-format" \
  --description "Triggers automatic code formatting" \
  --color "1D76DB" \
  --force || echo "Label 'auto-format' may already exist"

echo "✅ Labels created successfully!"
echo ""
echo "How it works:"
echo "  - 'ready-for-review' is added automatically when all CI checks pass"
echo "  - 'changes-required' is added automatically when CI checks fail"
echo "  - 'auto-format' can be added manually to trigger code formatting"
