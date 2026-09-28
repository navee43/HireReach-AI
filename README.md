# HR Outreach Generator

A small local Python script. You give it a list of HR/recruiter email addresses. For each company it:

1. works out the company from the email domain (and checks its website - it doesn't just guess from the domain),
2. researches the company with free tools,
3. guesses a greeting name from the address (and marks it as a guess),
4. fills your hardcoded prompt and asks an LLM to write a short personalised email,
5. saves everything to a CSV or Excel file (plus an easy-to-read TXT file).

**It never sends email.** It does not log in to Gmail or Outlook and does not use SMTP. You review each draft and send it yourself.

---

## 1. What you need

- **Python 3.9 or newer.** Check by typing `python --version` in a terminal.
- **VS Code** (any editor works).
- **One free LLM key.** Groq is the easiest to start with.
- **One free search key.** Tavily gives 1,000 free credits a month. Or use `website` mode, which needs no key.

---

## 2. Setup (one time)

1. Unzip the folder.
2. In VS Code, click **File > Open Folder...** and choose `hr-outreach-generator`.
3. Click **Terminal > New Terminal**.
4. Create a virtual environment and install the libraries:

   **Windows**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   **Mac / Linux**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   You'll see `(.venv)` at the start of the terminal line when it's active. Activate it again each time you open a new terminal.

---

## 3. Get your free keys

**Groq (LLM)**
1. Go to https://console.groq.com and sign in.
2. Click **API Keys**, then **Create API Key**.
3. Copy the key (it starts with `gsk_`).

**Tavily (search)**
1. Go to https://app.tavily.com and sign up (no card needed).
2. Copy your API key (it starts with `tvly-`).

---

## 4. Create your `.env` file (where your keys go)

1. In the VS Code file list, right-click `.env.example`, choose **Copy**, then right-click the folder and choose **Paste**.
2. Rename the copy to exactly `.env`.
3. Open `.env` and paste your keys:

   ```text
   LLM_PROVIDER=groq
   LLM_API_KEY=gsk_your_key_here
   LLM_MODEL=llama-3.3-70b-versatile
   LLM_BASE_URL=

   SEARCH_PROVIDER=tavily
   SEARCH_API_KEY=tvly-your_key_here
   ```

`.env` is listed in `.gitignore`, so it is never uploaded to GitHub. Don't put keys in `config.py`.

**Check that both keys work** (uses 1 LLM call and 1 search credit):

```bash
python main.py --test
```

---

## 5. Edit your email (once)

Open `config.py` and scroll to the bottom.

- **`EMAIL_SUBJECT_TEMPLATE`** and **`EMAIL_BODY_TEMPLATE`** are your email, used word for word.
- **`EMAIL_SIGNATURE`** is added under every email. Replace the `[...]` parts with your links and phone number. Until you do, the `notes` column warns you.
- **`EMAIL_PROMPT_TEMPLATE`** tells the LLM how to write the two company phrases.

The program fills in only these placeholders in the subject and body:

| Placeholder | Filled with |
|---|---|
| `{hr_first_name}` | e.g. `Rahul`, or `Hiring Team` / `HR Team` / `Recruitment Team` for shared inboxes |
| `{company_name}` | The company name found by research |
| `{company_interest}` | Written by the LLM from the research, e.g. "your platform that helps small businesses accept payments" |
| `{company_focus}` | Written by the LLM from the research, e.g. "making UPI payments reliable at scale" |

The two company phrases are written **once per company** and reused for every contact there. If the research is too thin, simple generic phrases are used instead, and the `notes` column tells you.

**Want the LLM to write the whole email instead?** Set `EMAIL_BODY_TEMPLATE = ""`. `EMAIL_PROMPT_TEMPLATE` then has to ask for a full email, and it can use `{hr_name}`, `{hr_first_name}`, `{hr_name_note}`, `{company_name}`, `{company_domain}`, `{company_research}`, `{company_specific_interest}`, `{careers_page}` and `{max_words}`.

---

## 6. Add email addresses

Put them in `input/emails.txt`, one per line. Lines starting with `#` are ignored.

```text
aman@company.com
rahul.sharma@company.com
careers@startup.io
```

Or use a CSV file with an `email` column, and point the program to it:

- set `INPUT_FILE = "input/emails.csv"` in `config.py`, or
- run `python main.py --input input/emails.csv`

The program removes duplicates and invalid addresses for you. Addresses at the same company are researched only once.

---

## 7. Run it

Try a few emails first:

```bash
python main.py --limit 3
```

Then run the whole list:

```bash
python main.py
```

You'll see progress like this:

```text
Researching companies... (65 unique domain(s))
[1/65] amazon.com -> Amazon
[2/65] company.com -> Example Company
...
Generating personalized emails...
[1/93] Aman - Amazon
[12/93] failed - someone@deadsite.com
       Reason: research unavailable: company website unavailable
       Continuing...
```

Other options:

| Command | What it does |
|---|---|
| `python main.py --format xlsx` | Write Excel instead of CSV (or set `OUTPUT_FORMAT = "xlsx"`) |
| `python main.py --no-cache` | Research companies again, even ones already saved |
| `python main.py --test` | Check your keys, then exit |

---

## 8. Find your results

The results go in the `output/` folder:

- `personalized_hr_emails.csv` (or `.xlsx`): one row per address.
- `personalized_hr_emails.txt`: every email laid out for easy reading and copying.
- `research_cache.json`: saved company research, reused for 30 days so re-runs don't use credits.
- `run_log.txt`: details for troubleshooting (keys are masked).

**The `status` column**

| Status | Meaning |
|---|---|
| `success` | Company researched and email written |
| `partial` | Email written without company info (only if `GENERATE_WITHOUT_RESEARCH = True`) |
| `failed` | Not written. The `notes` column says why |

**The `hr_name_source` column**

| Value | Meaning |
|---|---|
| `inferred_from_email` | A guess from the address, like `aman@` -> "Aman". Check it before sending |
| `generic_email` | A team inbox such as `hr@` or `careers@`, so the email greets "HR Team", "Hiring Team" or "Recruitment Team" |
| `unclear_email` | The address couldn't be read as a name (like `a.sharma@`), so it greets "Hiring Team" |

The spreadsheet has every column you asked for, plus a final `notes` column that explains failures and warnings.

---

## 9. Switching the LLM provider

Change these lines in `.env`. No code changes are needed.

| Provider | `LLM_PROVIDER` | Where to get a key | Example `LLM_MODEL` |
|---|---|---|---|
| Groq (free tier) | `groq` | console.groq.com | `llama-3.3-70b-versatile` or `llama-3.1-8b-instant` |
| OpenRouter | `openrouter` | openrouter.ai/keys | any model whose name ends in `:free` |
| Hugging Face | `huggingface` | huggingface.co/settings/tokens | e.g. `openai/gpt-oss-120b` |
| Google Gemini (free tier) | `gemini` | aistudio.google.com | copy a model name from AI Studio |
| Ollama (free, runs on your PC) | `ollama` | no key needed | e.g. `llama3.2` (run `ollama pull llama3.2` first) |
| LM Studio (free, runs on your PC) | `lmstudio` | no key needed | the model id shown in LM Studio |
| OpenAI (paid) | `openai` | platform.openai.com | any chat model |
| Any other OpenAI-style API | `openai_compatible` | your provider | your provider's model; also set `LLM_BASE_URL` (usually ends in `/v1`) |

Leave `LLM_BASE_URL` blank for everything except `openai_compatible`. Run `python main.py --test` after switching.

**Adding a new provider:** if it has an OpenAI-style API, add one line to `PROVIDERS` in `llm/manager.py`. For a different API, add a small adapter class like `llm/gemini.py`. The rest of the program only ever calls `llm.generate(prompt)`.

---

## 9b. Using several Groq keys (automatic rotation)

If your Groq account has several API keys, the tool can rotate between them automatically.

1. In `.env`, set `LLM_PROVIDER=groq` and leave `LLM_API_KEY=` empty.
2. Add your keys, numbered from 1. There can be any number of them, and gaps are fine:
   ```text
   GROQ_API_KEY_1=gsk_...
   GROQ_API_KEY_2=gsk_...
   ...
   GROQ_API_KEY_11=gsk_...
   ```
3. Run `python main.py --test`. You should see `Groq (openai/gpt-oss-120b, 11 keys)`.

**How it behaves**
- It keeps using one key while that key works (`[Groq] Using KEY_1`).
- When Groq answers **429**, it reads Groq's reset time from the `retry-after` header, the "Please try again in ..." message, or the `x-ratelimit-reset-*` headers. It rests that key until then and retries the **same request** with the next key (`[Groq] KEY_1 rate limited (tokens per minute) - available again in 8s`, then `[Groq] Switching to KEY_2`).
- A rested key comes back automatically once its time is up (`[Groq] KEY_1 available again`).
- A key Groq rejects (401/403) is switched off for the rest of the run.
- If **every** key is resting, it waits for the earliest one when that's within `GROQ_MAX_WAIT_SECONDS` (config.py, default 120s). Otherwise it stops cleanly with a clear message, and your results and research cache are saved.
- Problems with the request itself (wrong model, request too big for your limit) are reported straight away instead of being tried on every key.
- Every request has a hard cap on attempts, so it can never loop forever. It's also safe with requests running in parallel.
- Keys only ever appear in logs as `KEY_1`, `KEY_2` ... and never as the secret.

**Testing without touching the real API:** `python tests/test_groq_key_pool.py` runs 16 simulated scenarios (rate limits, daily limits, revoked keys, all keys busy, server errors, parallel requests) with a fake Groq server and a fake clock.

---

## 10. Choosing the research provider

Set `SEARCH_PROVIDER` in `.env`:

| Value | Key? | Notes |
|---|---|---|
| `tavily` | yes (free: 1,000 credits/month) | Best results. A new company usually costs 1-2 credits |
| `firecrawl` | yes (free starter credits) | Same idea as Tavily |
| `website` | **no** | Free. Reads only the company's own website, with no web search, so it finds less |

Company websites are always read directly first, which is free. A search API's page-reading feature (which costs credits) is only used when a site blocks normal requests. Sites whose `robots.txt` asks bots to stay out are skipped.

**Rough usage:** 100 emails across 60 companies costs about 60-120 search credits and about 120 LLM calls (2 per company, since each email reuses its company's phrases). Re-runs use the cache.

---

## 11. Safety built in

- **Never sends email.**
- **Never spends money on its own.** If a provider says your free credits or daily quota are used up (HTTP 402, 429 daily limit, or Tavily 432/433), it stops using that provider and tells you. If it's the search API, the run continues with website-only research.
- **Keys stay private.** They're read from `.env`, never put into prompts, and masked in the terminal and log.
- **Web pages can't give it orders.** Website and search text is fenced off as untrusted data, and the LLM is told never to follow instructions inside it.
- **No made-up facts.** The prompts forbid inventing facts or job openings. Careers-page links are only kept if they appeared in the research.
- **Spreadsheet-safe output.** Web text can't turn into Excel formulas in the CSV or XLSX files.
- **Failures don't stop the run.** One bad contact is marked `failed` and the run continues. Temporary errors are retried 3 times (waiting 2s, 4s, then 8s). An invalid key stops the run immediately instead of retrying. Press Ctrl+C at any time and the results so far are still saved.

**Use it responsibly.** Only use addresses that companies publish or that recruiters share for applications. Read and edit every draft before sending, send them yourself a few at a time, and stop contacting anyone who asks you to.

---

## 12. Troubleshooting

| Message | Fix |
|---|---|
| `LLM_API_KEY is empty` | Create `.env` (step 4) and paste your key |
| `rejected the API key (HTTP 401)` | The key was copied wrongly or revoked. Make a new one |
| `model or endpoint not found (HTTP 404)` | Check the `LLM_MODEL` spelling against your provider's model list |
| `rate limit (HTTP 429)` | The free tier's per-minute limit. The program retries on its own; you can raise `REQUEST_DELAY_SECONDS` in config.py |
| `free-tier limit reached` | Wait until the limit resets, or switch provider/model |
| `SEARCH_API_KEY is empty` | Add the key, or set `SEARCH_PROVIDER=website` |
| `research unavailable` | The site couldn't be reached and search found nothing. Set `GENERATE_WITHOUT_RESEARCH = True` to still get a generic draft |
| `Personal email provider` | gmail.com and similar addresses don't reveal the employer. Same fix as above |
| Output file won't save | Close it in Excel. If it's locked, the program saves a copy with a timestamp in the name |

---

## Project layout

```text
main.py                 runs the pipeline and prints progress
config.py               all settings + your prompt and signature
llm/                    provider-independent LLM layer (LLMManager + adapters)
research/               search / page-reading providers (Tavily, Firecrawl, website-only)
services/
  email_parser.py       read, validate, de-duplicate, group by domain
  name_extractor.py     greeting-name guesses (never marked as verified)
  company_research.py   research once per domain, summarise, cache
  email_generator.py    fill the prompt, get JSON from the LLM, validate it
  exporter.py           CSV / XLSX / TXT output
utils/                  logging (with key masking) and small helpers
input/emails.txt        your address list
output/                 results appear here
```
