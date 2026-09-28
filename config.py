"""
HR Outreach Generator - all settings live here.

* API keys go in the .env file (copy .env.example to .env). Values in .env
  override the defaults below, so you rarely need to touch the LLM lines here.
* Edit EMAIL_PROMPT_TEMPLATE and EMAIL_SIGNATURE at the bottom once.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    return value.strip() if value.strip() else default






# ------------------------------------------------------------------ Files
INPUT_FILE = "input/emails.txt"     # .txt (one email per line) or .csv (with an "email" column)
OUTPUT_DIR = "output"
OUTPUT_FORMAT = "csv"               # "csv" or "xlsx"
ALSO_WRITE_TXT = True               # also write an easy-to-read .txt file

# ------------------------------------------------------------------ LLM
# groq | openrouter | huggingface | gemini | ollama | lmstudio | openai | openai_compatible
LLM_PROVIDER = _env("LLM_PROVIDER", "groq")
LLM_API_KEY = _env("LLM_API_KEY")                     # put this in .env, not here
LLM_MODEL = _env("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_BASE_URL = _env("LLM_BASE_URL")                   # blank = the provider's default URL

# ------------------------------------------------------------------ Research
# tavily | firecrawl | website  ("website" = free, no key, reads each company's own site only)
SEARCH_PROVIDER = _env("SEARCH_PROVIDER", "tavily")
SEARCH_API_KEY = _env("SEARCH_API_KEY")               # put this in .env, not here

# ------------------------------------------------------------------ Email generation
TEMPERATURE = 0.7
MAX_EMAIL_LENGTH = 180              # target maximum words in the email body
GENERATE_WITHOUT_RESEARCH = False   # True = still draft a generic email when a company can't be
                                    # researched (e.g. gmail.com addresses); marked status "partial"
USE_LLM_FOR_RESEARCH_SUMMARY = True # False = summarise companies with simple rules (fewer LLM calls)

# ------------------------------------------------------------------ Limits (free tiers)
REQUEST_DELAY_SECONDS = 1.5         # minimum gap between LLM calls
MAX_RETRIES = 3                     # attempts for temporary errors (rate limits, timeouts)
RETRY_BASE_DELAY_SECONDS = 2.0      # backoff: 2s, 4s, 8s ...
HTTP_TIMEOUT_SECONDS = 30
LLM_TIMEOUT_SECONDS = 90
MAX_RESEARCH_CHARS = 6000           # how much web text is sent to the LLM per company

# ------------------------------------------------------------------ Research cache
CACHE_RESEARCH = True               # reuse company research across runs (saves credits)
CACHE_MAX_AGE_DAYS = 30
CACHE_FILE = "output/research_cache.json"

GROQ_MAX_WAIT_SECONDS = 120         # if every key is resting, wait up to this long; longer -> stop cleanly
GROQ_MAX_ATTEMPTS_PER_REQUEST = 0   # 0 = automatic (2 x number of keys + 2)

# ================================================================== YOUR EMAIL
# FIXED FORMAT: the subject and body below are used word for word.
# The program fills in:
#   {hr_first_name}     e.g. "Rahul", or "Hiring Team" for inboxes like careers@
#   {company_name}      the company name found by research
#   {company_interest}  written by the LLM from the research (sentence 1)
#   {company_focus}     written by the LLM from the research (sentence 2)
# To let the LLM write the whole email instead, set EMAIL_BODY_TEMPLATE = "".

EMAIL_SUBJECT_TEMPLATE = "Exploring Software Development / Full Stack / GenAI Opportunities at {company_name}"

EMAIL_BODY_TEMPLATE = """Hi {hr_first_name},

I hope you're doing well.

I recently came across {company_name} and was really interested in {company_interest}. I particularly appreciate how your team is working on {company_focus}.

I'm Naveen Yadav, a final-year B.Tech IT student at IIIT Una, with hands-on experience in Full Stack Development and Generative AI. Through my internships, I've worked with React, Node.js, Express, REST APIs, and LLM integrations. I've also built an Autonomous Coding Agent using LangGraph and have experience with GitHub Actions for CI/CD.

Alongside development, I actively practice Data Structures and Algorithms, achieving a 1900+ rating on LeetCode (Knight) and a 1600+ rating on Codeforces (Specialist), further strengthening my problem-solving skills.

I'd love to know if there are any opportunities at {company_name} for Software Developer, Full Stack/Web Developer, or GenAI Developer roles where I could contribute and grow with your team.

I've attached my resume for your reference. Even if there aren't any suitable openings right now, I'd be grateful if you could consider my profile for future opportunities.

Looking forward to hearing from you!"""

# Added under every email exactly as written. Replace the [...] parts with your details.
EMAIL_SIGNATURE = """Best regards,
Naveen Yadav
LinkedIn: https://www.linkedin.com/in/naveen-yadav-36122b29a/
GitHub: https://github.com/navee43
LeetCode: https://leetcode.com/u/_Naveen11k/
Codeforces: https://codeforces.com/profile/xorlax 
Phone: 7015057676"""

# In FIXED format the LLM only writes the two company phrases, using this prompt.
# (If EMAIL_BODY_TEMPLATE is "", this prompt must instead ask for the whole email -
#  see README section 5.)
# Placeholders: {company_name} {company_domain} {company_research} {company_specific_interest}
EMAIL_PROMPT_TEMPLATE = """
TASK
My outreach email to {company_name} contains these two sentences:
  1. "I recently came across {company_name} and was really interested in <COMPANY_INTEREST>."
  2. "I particularly appreciate how your team is working on <COMPANY_FOCUS>."
Write the two missing phrases using ONLY the company research below.

COMPANY RESEARCH
Company: {company_name} ({company_domain})
Specific point found: {company_specific_interest}
Research notes:
{company_research}

RULES
- Each phrase: 6-15 words, starts with a lowercase word (unless it begins with a product
  name), no full stop at the end, and reads naturally inside its sentence.
- COMPANY_INTEREST: what the company does - its product, platform or mission
  (e.g. "your platform that helps small businesses accept online payments").
- COMPANY_FOCUS: a specific technology, product or problem they work on
  (e.g. "making UPI payments reliable at very large scale").
- The two phrases must say different things. Address the company as "your", not "their".
- Use only facts from the research notes. No flattery words like "amazing",
  "revolutionary" or "world-class".
- If the research is thin, keep both phrases simple and general, e.g. "your products"
  and "the problems you solve for your customers".
"""