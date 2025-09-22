# AI Assistant Integration for Kamiwaza SDK

This directory implements a "Rules as Code" pattern for AI-assisted development on the Kamiwaza Python SDK, providing tool-agnostic guidance that works across Claude, Cursor, Windsurf, GitHub Copilot, and other AI coding assistants.

## Overview

The `.ai/` directory complements the existing CLAUDE.md documentation with:
- **Tool-agnostic rules** that any AI assistant can reference
- **SDK-specific prompts** for common development tasks
- **Knowledge capture** of successful patterns and pitfalls in SDK development

## Directory Structure

```
.ai/
├── rules/              # SDK coding standards and patterns
│   ├── core-principles.md    # YAGNI, KISS, and fundamental principles
│   ├── style.md             # Python style and SDK conventions
│   ├── sdk-patterns.md      # Architecture and design patterns
│   └── testing.md           # Testing requirements and patterns
├── prompts/            # Task-specific prompt templates
│   ├── add-service.md       # Template for adding new API services
│   ├── add-mixin.md         # Template for extending services with mixins
│   ├── fix-test.md          # Template for debugging test failures
│   ├── debug-auth.md        # Template for auth troubleshooting
│   └── optimize-performance.md # Template for performance improvements
└── knowledge/          # Captured SDK development knowledge
    ├── successful/     # Proven patterns that work well
    │   └── service-patterns.md  # Successful SDK design patterns
    └── failures/       # Known pitfalls and how to avoid them
        └── common-pitfalls.md   # Common SDK development mistakes
```

## How Different AI Tools Use This

### Claude (via Claude Code)
- Reads CLAUDE.md files for context automatically
- Reference specific rules: "Follow the rules in @.ai/rules/sdk-patterns.md"
- Use prompts as conversation starters: "Use @.ai/prompts/add-service.md template"

### Cursor
- Automatically indexes the .ai/ directory
- Reference rules with `@.ai/rules/style.md`
- Include prompts with `@.ai/prompts/add-mixin.md`

### Windsurf
- Similar to Cursor, indexes .ai/ directory
- Can reference files directly in chat
- Rules provide context for code generation

### GitHub Copilot
- Workspace includes .ai/ directory
- Rules influence code suggestions
- Comments can reference rule files

### VS Code with Other Extensions
- Most AI extensions can read workspace files
- Reference rules in comments or chat
- Copy prompts into extension interfaces

## SDK-Specific Usage Examples

### 1. Adding a New Service
```bash
# Reference the prompt template
"I need to add a new service following @.ai/prompts/add-service.md"

# Fill in the template variables:
- SERVICE_NAME: embeddings
- DESCRIPTION: handles text embedding generation
- METHOD: POST
- PATH: /embeddings/generate
```

### 2. Extending a Service with Mixins
```bash
# Use the mixin template
"Add batch processing to models service using @.ai/prompts/add-mixin.md"

# The AI will:
- Create a new mixin following SDK patterns
- Add it to the service composition
- Include proper type hints and tests
```

### 3. Debugging Authentication Issues
```bash
# Reference auth debugging guide
"I'm getting 401 errors, help me debug using @.ai/prompts/debug-auth.md"
```

### 4. Fixing Test Failures
```bash
# Get help with failing tests
"Test test_download_model is failing with @.ai/prompts/fix-test.md"
```

## Key SDK Patterns to Remember

### Service Architecture
- **Lazy Loading**: Services created on first access
- **Mixin Composition**: Complex services built from focused mixins  
- **BaseService**: All services inherit common functionality

### Data Models
- **Pydantic Everywhere**: All API contracts use Pydantic models
- **Forward Compatibility**: Models allow extra fields with `extra = "allow"`
- **Type Safety**: Comprehensive type hints throughout

### Error Handling
- **Semantic Exceptions**: HTTP errors mapped to meaningful exceptions
- **Automatic Retry**: Transient errors retried with backoff
- **Auth Refresh**: 401s trigger automatic token refresh

## Benefits of This Approach

1. **Consistency**: Same patterns across all AI tools
2. **Discoverability**: Easy to find relevant guidance
3. **Learning**: Captured knowledge prevents repeated mistakes
4. **Efficiency**: Prompt templates speed up common tasks
5. **Quality**: Rules ensure SDK best practices

## Relationship to CLAUDE.md

This `.ai/` directory structure complements the existing CLAUDE.md files:

- **CLAUDE.md**: High-level architecture and development commands
- **.ai/rules/**: Concrete SDK patterns and standards
- **.ai/prompts/**: Task-specific templates for SDK work
- **.ai/knowledge/**: Lessons learned from SDK development

Together, they create a comprehensive AI assistance framework for SDK development.

## Contributing

When adding new content:

1. **Rules**: Focus on SDK-specific patterns and standards
2. **Prompts**: Create templates for repetitive SDK tasks
3. **Knowledge**: Document real issues and solutions
4. **Keep it DRY**: Don't duplicate content between files

## Quick Reference

### Most Important Rules
- @.ai/rules/core-principles.md - YAGNI, KISS, one task at a time
- @.ai/rules/sdk-patterns.md - Service architecture and patterns
- @.ai/rules/style.md - Python style and conventions

### Common Tasks
- @.ai/prompts/add-service.md - Adding new API services
- @.ai/prompts/add-mixin.md - Extending services
- @.ai/prompts/fix-test.md - Debugging test failures

### Learn from Experience
- @.ai/knowledge/successful/service-patterns.md - What works well
- @.ai/knowledge/failures/common-pitfalls.md - What to avoid

---

*This is a living system. As we learn what works in SDK development, we'll evolve these patterns.*