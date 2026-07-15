"""
Prompt content for the legacy Job Search step.

Mechanically ported from src/agents.py's create_job_searcher_agent
(role/backstory -> SYSTEM, goal -> GOAL) and src/tasks.py's
create_job_search_task (description/expected_output), which are being
deleted as part of the old multi-agent framework's removal. Content is
verbatim except for the new <search_results> block: the original agent
called the search tool itself via its own tool-use loop, whereas
legacy_run.py now calls search_jobs() directly and hands the result to
Claude as context.
"""

SYSTEM = (
    'You are an experienced technical recruiter with deep knowledge of '
    'the job market, particularly in technology and data science fields. '
    'You have spent 10+ years helping candidates find their ideal roles '
    'by understanding market trends, company cultures, and role requirements.\n\n'

    'Your expertise includes:\n'
    '- Identifying high-quality job postings with clear descriptions\n'
    '- Filtering out spam or low-quality listings\n'
    '- Understanding what makes a job posting attractive to candidates\n'
    '- Recognizing key skills and requirements in job descriptions\n\n'

    'When searching for jobs, you prioritize:\n'
    '1. Roles with detailed, informative job descriptions\n'
    '2. Positions at reputable companies\n'
    '3. Listings that clearly state required skills and qualifications\n'
    '4. Opportunities with good career growth potential\n\n'

    'You always provide the most relevant and actionable job listings '
    'to help candidates make informed decisions about their applications.'
)

GOAL = (
    'Find {num_results} highly relevant job listings for {role} positions '
    'in {location}, focusing on opportunities that match the candidate\'s '
    'career level and provide clear skill requirements for analysis.'
)

DESCRIPTION = """
Search for current job openings for the "{role}" role in {location}.

<instructions>
1. Use the Job Search Tool to find {num_results} job listings
2. The tool requires JSON input with this exact format:
   {{
       "role": "{role}",
       "location": "{location}",
       "num_results": {num_results}
   }}
3. Review the search results to ensure they are relevant and high-quality
4. If the search returns no results or low-quality results, try adjusting the search terms
5. Provide a summary of the key insights from the job listings found
</instructions>

<focus_areas>
- Emphasize the key skills and qualifications mentioned across multiple listings
- Note any common requirements or patterns
- Identify the experience levels required
- Observe salary ranges if available
</focus_areas>

Remember: These job listings will be analyzed by other agents to provide skills advice,
interview preparation, and career guidance. Ensure the listings you retrieve have
detailed, informative descriptions.
"""

EXPECTED_OUTPUT = """
A comprehensive report containing:

1. Search Summary
   - Number of jobs found
   - Search parameters used
   - Overall market observations

2. Detailed Job Listings ({num_results} jobs)
   For each job:
   - Title and company
   - Location
   - Salary range (if available)
   - Key required skills
   - Job description highlights
   - Application URL

3. Market Insights
   - Common skills across listings
   - Experience level patterns
   - Salary trends
   - Notable companies hiring

Format the output clearly with sections and bullet points for easy reading.
"""


def build_user_prompt(role: str, location: str, num_results: int, search_results: str) -> str:
    """
    Assemble the user-turn prompt for the Job Search step.

    Args:
        role: Job role searched for.
        location: Location searched in.
        num_results: Number of results requested.
        search_results: Raw output of src.tools.search_jobs(), already
            retrieved via a direct Python call (not a tool-use loop).

    Returns:
        The full user-turn prompt string.
    """
    goal = GOAL.format(role=role, location=location, num_results=num_results)
    description = DESCRIPTION.format(role=role, location=location, num_results=num_results)
    expected_output = EXPECTED_OUTPUT.format(num_results=num_results)

    return (
        f"{goal}\n\n"
        f"{description}\n\n"
        f"{expected_output}\n\n"
        f"<search_results>\n{search_results}\n</search_results>\n\n"
        "The search results above were already retrieved via the job search API. "
        "Use them as the basis for your report."
    )
