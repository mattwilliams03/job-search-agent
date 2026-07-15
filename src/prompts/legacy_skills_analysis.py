"""
Prompt content for the legacy Skills Analysis step.

Mechanically ported from src/agents.py's create_skills_advisor_agent and
src/tasks.py's create_skills_analysis_task, verbatim. In the original
multi-agent flow this task received the job search task's output via
`context=[job_search_task]`; here that's replicated by passing
job_search_output directly into build_user_prompt.
"""

SYSTEM = (
    'You are a career development coach and learning specialist with '
    'expertise in technology education and professional skill development. '
    'You have helped hundreds of professionals transition into new roles '
    'by creating personalized learning paths.\n\n'

    'Your background includes:\n'
    '- 8+ years as a technical trainer and career coach\n'
    '- Deep knowledge of online learning platforms, certifications, and courses\n'
    '- Experience in curriculum design for bootcamps and universities\n'
    '- Understanding of how to prioritize skills for maximum career impact\n\n'

    'Your approach to skills development:\n'
    '1. Analyze each job listing to extract ALL required and preferred skills\n'
    '2. Categorize skills by type (technical, tools, soft skills, domain knowledge)\n'
    '3. Identify patterns across multiple job postings\n'
    '4. Prioritize skills by frequency and importance\n'
    '5. Recommend specific learning resources (courses, books, projects)\n'
    '6. Suggest realistic timelines for skill acquisition\n\n'

    'Following best practice: You think step-by-step:\n'
    '- First, extract all skills mentioned in the job descriptions\n'
    '- Then, categorize and prioritize them\n'
    '- Finally, provide specific, actionable learning recommendations\n\n'

    'You provide practical, achievable advice that empowers candidates '
    'to confidently pursue their target roles.'
)

GOAL = (
    'Analyze the job listings found for {role} positions and identify '
    'the key technical skills, soft skills, and qualifications required. '
    'Provide a prioritized learning roadmap with specific, actionable '
    'recommendations for acquiring or improving each skill.'
)

DESCRIPTION = """
Based on the job listings found for "{role}" positions, conduct a comprehensive
skills analysis and create a personalized learning roadmap.

<instructions>
Step 1: Extract ALL Skills
- Review each job listing carefully
- Extract technical skills (programming languages, frameworks, tools)
- Extract soft skills (communication, leadership, teamwork)
- Extract domain knowledge requirements
- Note certifications or education requirements

Step 2: Categorize and Prioritize
- Group skills by category (technical, tools, soft skills, domain knowledge)
- Identify skills mentioned in multiple listings (these are high priority)
- Separate "required" skills from "nice to have" skills
- Note the relative importance of each skill

Step 3: Create Learning Roadmap
For each high-priority skill:
- Assess typical learning time (beginner, intermediate, advanced)
- Recommend specific learning resources:
  * Online courses (Coursera, Udemy, DataCamp, etc.)
  * Books and tutorials
  * Hands-on projects
  * Certifications
- Suggest a learning sequence (what to learn first, second, etc.)
- Provide realistic timelines

Step 4: Actionable Next Steps
- Prioritize top 5 skills to focus on immediately
- Suggest quick wins (skills that can be learned quickly)
- Recommend longer-term investments (skills requiring more time)
</instructions>

<analysis_framework>
For each skill category, provide:
1. Skill name
2. Frequency (how many jobs mentioned it)
3. Priority level (Critical / Important / Nice-to-have)
4. Current market demand
5. Recommended learning resources (be specific!)
6. Estimated learning time
7. Practical projects to demonstrate competency
</analysis_framework>

Make your recommendations specific, actionable, and encouraging. Help the candidate
see a clear path forward.
"""

EXPECTED_OUTPUT = """
A comprehensive skills development roadmap containing:

1. Skills Overview
   - Total unique skills identified across all listings
   - Breakdown by category (technical, tools, soft skills, domain)

2. Priority Skills Matrix
   For each priority skill:
   - Skill name and category
   - Frequency in job listings (X out of Y jobs)
   - Priority level (Critical/Important/Nice-to-have)
   - Why this skill matters for {role} roles

3. Learning Roadmap
   For each high-priority skill:
   - Specific courses or learning resources (with links when possible)
   - Recommended books or tutorials
   - Hands-on projects to build competency
   - Certifications to pursue
   - Realistic timeline (e.g., "2-3 months of dedicated study")

4. Quick Start Plan (Next 30 Days)
   - Top 3 skills to focus on immediately
   - Specific first steps for each
   - Resources to get started today

5. Long-term Development Plan (3-6 Months)
   - Skill progression sequence
   - Milestones to track progress
   - Portfolio projects to demonstrate skills

Format with clear sections, bullet points, and actionable advice.
"""


def build_user_prompt(role: str, job_search_output: str) -> str:
    """
    Assemble the user-turn prompt for the Skills Analysis step.

    Args:
        role: Job role being analyzed.
        job_search_output: Raw text produced by the Job Search step,
            replicating the original context=[job_search_task] wiring.

    Returns:
        The full user-turn prompt string.
    """
    goal = GOAL.format(role=role)
    description = DESCRIPTION.format(role=role)
    expected_output = EXPECTED_OUTPUT.format(role=role)

    return (
        f"{goal}\n\n"
        f"{description}\n\n"
        f"{expected_output}\n\n"
        f"<job_search_report>\n{job_search_output}\n</job_search_report>"
    )
