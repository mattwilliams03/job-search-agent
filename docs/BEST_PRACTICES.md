# Best Practices Applied in This Project

This document summarizes the best practices from Anthropic and software engineering that were applied in building this job search system.

## Table of Contents
- [Anthropic Claude Best Practices](#anthropic-claude-best-practices)
- [Sequential Pipeline Design Patterns](#sequential-pipeline-design-patterns)
- [Software Engineering Best Practices](#software-engineering-best-practices)
- [How We Applied These Practices](#how-we-applied-these-practices)

---

## Anthropic Claude Best Practices

### 1. Clear and Explicit Instructions

**Principle:** Claude performs best when instructions are specific, detailed, and unambiguous.

**Applied in our code:**
- Task descriptions in `tasks.py` use explicit, step-by-step instructions
- Agent goals are specific and measurable (e.g., "Find 5 highly relevant job listings")
- Tool input schemas are clearly defined with examples

**Example from our code:**
```python
# tasks.py - Clear, structured task description
description = """
Search for current job openings for the "{role}" role in {location}.

<instructions>
1. Use the Job Search Tool to find {num_results} job listings
2. The tool requires JSON input with this exact format:
   {{
       "role": "{role}",
       "location": "{location}",
       "num_results": {num_results}
   }}
3. Review the search results to ensure they are relevant
...
</instructions>
"""
```

### 2. XML Tags for Structure

**Principle:** Claude is trained to recognize XML-style tags, which help separate different types of content and improve parsing.

**Applied in our code:**
- Job listings formatted with `<job>`, `<title>`, `<company>` tags
- Task instructions use `<instructions>`, `<focus_areas>` tags
- Recommendations use `<recommendation>`, `<priority>` tags

**Example from our code:**
```python
# tools.py - XML-structured output
formatted = f"""
<job>
    <title>{title}</title>
    <company>{company}</company>
    <location>{location}</location>
    <description>{description}</description>
</job>
"""
```

### 3. Few-Shot Prompting (Examples)

**Principle:** Showing Claude examples of desired output significantly improves results, especially for nuanced tasks.

**Applied in our code:**
- Interview Coach agent includes example question format in backstory
- Task descriptions include expected output examples
- Agent backstories demonstrate the approach through examples

**Example from our code:**
```python
# agents.py - Example format in agent backstory
backstory=(
    '...'
    'Following Anthropic best practice - Example question format:\n'
    '<question>\n'
    '  <type>Technical</type>\n'
    '  <text>The actual interview question</text>\n'
    '  <guidance>How to answer</guidance>\n'
    '</question>\n'
)
```

### 4. Chain-of-Thought Reasoning

**Principle:** Asking Claude to think step-by-step leads to more thorough and accurate responses.

**Applied in our code:**
- Skills Advisor task explicitly uses "Step 1, Step 2, Step 3..." structure
- Agent verbose mode enabled to see thinking process
- Task descriptions encourage systematic analysis

**Example from our code:**
```python
# tasks.py - Step-by-step reasoning prompt
"""
Step 1: Extract ALL Skills
- Review each job listing carefully
- Extract technical skills, soft skills, domain knowledge

Step 2: Categorize and Prioritize
- Group skills by category
- Identify patterns across listings

Step 3: Create Learning Roadmap
- For each skill, recommend resources...
"""
```

### 5. Role and Persona Design

**Principle:** Giving Claude a clear role with expertise and context improves output quality and consistency.

**Applied in our code:**
- Each agent has a distinct role (Job Searcher, Skills Advisor, etc.)
- Rich backstories establish expertise and approach
- Agents have clear identities (e.g., "10+ years as technical recruiter")

**Example from our code:**
```python
# agents.py - Rich persona with expertise
backstory=(
    'You are an experienced technical recruiter with deep knowledge of '
    'the job market, particularly in technology and data science fields. '
    'You have spent 10+ years helping candidates find their ideal roles...'

    'Your expertise includes:\n'
    '- Identifying high-quality job postings\n'
    '- Understanding what makes roles attractive\n'
    '...'
)
```

### 6. Structured Output Formats

**Principle:** Explicitly defining the expected output format ensures consistent, parseable results.

**Applied in our code:**
- Every task has a detailed `expected_output` specification
- Output formats use clear section headers and bullet points
- Consistent formatting across all agent outputs

---

## Sequential Pipeline Design Patterns

### 1. Role-Based Step Architecture

**Principle:** Design each step with a specialized role that naturally decomposes complex tasks.

**Applied in our code:**
- 4 specialized steps, each with distinct expertise, defined in `src/prompts/`
- Clear separation of concerns (search vs analyze vs advise)
- Natural workflow: search → analyze skills → prep interviews → career advice

**Benefits:**
- Easier to maintain and debug
- Each step can be improved independently
- Clear delegation of responsibilities

### 2. Step Chaining Through Context

**Principle:** Steps collaborate by passing context from previous steps.

**Applied in our code:**
```python
# src/core/legacy_run.py - job search output passed into later prompts
output = llm.complete(
    task="legacy_skills_analysis",
    system=legacy_skills_analysis.SYSTEM,
    user=legacy_skills_analysis.build_user_prompt(role, job_search_output),
)
```

**Benefits:**
- Steps build on each other's work
- No need to repeat information
- Sequential refinement of analysis

### 3. Tool Pattern

**Principle:** Use tools to interact with external systems and APIs, called directly by code rather than left to model discretion when the call parameters are already known.

**Applied in our code:**
- `search_jobs` tool wraps Adzuna API
- Clean separation: `src/tools.py` (executor) vs `src/core/legacy_run.py` (caller)
- Reusable tool with clear input/output contract

**Example from our code:**
```python
# tools.py - plain function, called directly by legacy_run.py
def search_jobs(role: str, location: str, num_results: int) -> str:
    """
    Search for job listings using Adzuna API.
    Clear documentation of input schema and return format.
    """
```

### 4. Verbose Mode for Learning

**Principle:** Enable detailed output to understand each step's progress.

**Applied in our code:**
- `--verbose` CLI flag (see `main.py`, `src/cli.py`)
- Students can see each step's saved output
- Great for debugging and learning

### 5. Sequential Process for Complex Workflows

**Principle:** For tasks with dependencies, use sequential processing where each step completes before the next begins.

**Applied in our code:**
```python
# src/core/legacy_run.py - sequential step execution
for step_name, prompt_module in (
    ("skills_analysis", legacy_skills_analysis),
    ("interview_prep", legacy_interview_prep),
    ("career_advisory", legacy_career_advisory),
):
    output = llm.complete(task=f"legacy_{step_name}", ...)  # One step at a time
```

---

## Software Engineering Best Practices

### 1. Configuration Management

**Principle:** Separate configuration from code for flexibility and security.

**Applied in our code:**
- Centralized `config.py` with all settings
- Environment variables for API keys (.env file)
- Easy customization without touching code logic

### 2. Error Handling and Retry Logic

**Principle:** Production code must handle failures gracefully.

**Applied in our code:**
```python
# tools.py - Robust error handling with retries
def _make_api_request_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Handle rate limiting, server errors
            if response.status_code == 429:
                time.sleep(delay)
                continue
            # ...
```

### 3. Type Hints and Documentation

**Principle:** Code should be self-documenting and type-safe.

**Applied in our code:**
- Type hints on all functions
- Comprehensive docstrings
- Clear parameter and return value documentation

**Example:**
```python
def build_user_prompt(role: str, location: str, num_results: int, search_results: str) -> str:
    """
    Assemble the user-turn prompt for the Job Search step.

    Args:
        role: Job role searched for.
        location: Location searched in.
        num_results: Number of results requested.
        search_results: Raw output of src.tools.search_jobs().

    Returns:
        The full user-turn prompt string.
    """
```

### 4. Modular Code Structure

**Principle:** Organize code into logical modules with single responsibilities.

**Applied in our project:**
```
src/
├── config.py      # Configuration and settings
├── llm.py         # Thin Anthropic SDK wrapper
├── tools.py       # External tool integrations
├── prompts/       # Per-step system/user prompt content
├── core/          # Service functions (e.g. legacy_run.py)
├── db/            # SQLite connection, migrations, repos
├── cli.py         # typer CLI (`jobsearch` command)
└── __init__.py    # Module exports
```

### 5. Testing

**Principle:** Test critical functionality, especially external integrations.

**Applied in our code:**
- Unit tests for input validation
- Mocked tests for API integration
- Integration tests (optional) for full system

### 6. User Experience

**Principle:** Make it easy for users to get started and understand what's happening.

**Applied in our code:**
- Clear console output with progress indicators
- Helpful error messages with troubleshooting tips
- Example outputs to set expectations
- Comprehensive documentation

---

## How We Applied These Practices

### In Prompt Design (src/prompts/)

✅ **Clear roles** - Each step has a specific expertise area (SYSTEM prompt)
✅ **Rich backstories** - Detailed persona with experience and approach
✅ **Explicit goals** - Measurable objectives with context variables
✅ **Examples in prompts** - Interview Coach shows question format
✅ **Verbose mode** - Students can see each step's saved output

### In Step Design (src/core/legacy_run.py)

✅ **Step-by-step instructions** - Skills step uses explicit steps
✅ **XML-structured prompts** - Clear sections with tags
✅ **Context chaining** - Later steps build on the job search step's results
✅ **Expected output defined** - Clear format specifications
✅ **Per-step file output** - Save intermediate results

### In Tool Design (tools.py)

✅ **XML-formatted output** - Job listings in structured tags
✅ **Clear input schema** - JSON format explicitly documented
✅ **Robust error handling** - Graceful failures with helpful messages
✅ **Retry logic** - Handle rate limits and transient errors
✅ **Type hints** - All functions have type annotations

### In Project Structure

✅ **Configuration management** - Centralized settings
✅ **Environment variables** - Secure API key handling
✅ **Modular architecture** - Clear separation of concerns
✅ **Comprehensive testing** - Unit and integration tests
✅ **Documentation** - README, SETUP, TROUBLESHOOTING
✅ **Examples** - Sample outputs to set expectations

---

## Key Takeaways for Students

### When Building Your Own Agents:

1. **Be Specific** - Vague prompts get vague results. Clear instructions get clear results.

2. **Use Structure** - XML tags, numbered steps, and sections help Claude parse and respond better.

3. **Show Examples** - Don't just describe what you want; show an example of it.

4. **Think Step-by-Step** - Break complex tasks into sequential steps.

5. **Give Rich Context** - Agent backstories with expertise and personality improve outputs.

6. **Handle Errors** - Production code needs retry logic and graceful error handling.

7. **Test Your Code** - Especially external API integrations.

8. **Document Everything** - Future you (and other students) will thank you.

### Resources for Learning More

**Anthropic Documentation:**
- Prompt Engineering Guide: https://docs.anthropic.com/prompt-engineering
- Claude API Documentation: https://docs.anthropic.com/claude/reference

**General Resources:**
- Adzuna API Docs: https://developer.adzuna.com/
- Python Type Hints: https://docs.python.org/3/library/typing.html
- pytest Documentation: https://docs.pytest.org/

---

## Experiment and Iterate!

These best practices are guidelines, not strict rules. The best way to learn is to:

1. **Start with this working example**
2. **Modify one thing at a time** (change a prompt, add a tool, etc.)
3. **Observe how it affects the output**
4. **Iterate based on results**

Building great AI agents is an iterative process. Don't be afraid to experiment!

---

**Questions?** Come to office hours or post in the Discord!

**Want to contribute?** Submit a PR with your improvements!
