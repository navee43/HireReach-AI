"""
HR Outreach Generator - drafts personalised outreach emails. It NEVER sends email.

    python main.py              run on input/emails.txt (see config.py)
    python main.py --test       check your LLM and search keys (1-2 API calls)
    python main.py --limit 5    try the first 5 emails only
    python main.py --input input/emails.csv --format xlsx
    python main.py --no-cache   research companies again even if cached
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

import config
from llm.base import FATAL_LLM_ERRORS, LLMError, LLMRateLimitError
from llm.manager import PROVIDERS, LLMManager
from research.base import ResearchAuthError, ResearchError
from research.manager import get_research_provider, test_research
from services.company_research import CompanyProfile, CompanyResearcher
from services.email_generator import generate_email
from services.email_parser import (InputFileError, get_domain, group_by_domain, read_emails,
                                   validate_and_dedupe)
from services.exporter import BASE_NAME, export
from services.name_extractor import NameGuess, guess_name
from utils import logger
from utils.helpers import clean_text

PROJECT_DIR = Path(__file__).resolve().parent


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_DIR / p


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft personalised HR outreach emails (never sends them).")
    parser.add_argument("--test", action="store_true", help="check the LLM and research provider, then exit")
    parser.add_argument("--limit", type=int, default=0, help="only process the first N emails")
    parser.add_argument("--input", default="", help="input file (default: INPUT_FILE in config.py)")
    parser.add_argument("--format", choices=["csv", "xlsx"], default="", help="output format")
    parser.add_argument("--no-cache", action="store_true", help="ignore saved company research")
    return parser.parse_args()


def make_row(email: str, name: Optional[NameGuess], profile: Optional[CompanyProfile], llm_label: str,
             llm_model: str, status: str, notes: str = "", subject: str = "", body: str = "",
             interest: str = "") -> Dict[str, str]:
    usable = profile is not None and profile.usable
    return {
        "hr_name": name.name if name else "",
        "hr_name_source": name.source if name else "",
        "hr_email": email,
        "company_name": profile.company_name if profile else "Unknown",
        "company_domain": profile.domain if profile else (email.rsplit("@", 1)[-1] if "@" in email else ""),
        "company_website": profile.website if usable else "",
        "company_summary": profile.summary() if usable else "",
        "company_interest": (profile.company_interest if usable and profile.company_interest else interest),
        "careers_page": profile.careers_page if usable else "",
        "research_sources": "; ".join(profile.sources) if usable else "",
        "email_subject": subject,
        "email_body": body,
        "llm_provider": llm_label,
        "llm_model": llm_model,
        "status": status,
        "notes": notes,
    }


def run_tests(llm: LLMManager, provider) -> int:
    ok = True
    logger.info("Testing LLM...")
    try:
        reply = llm.test()
        logger.info(f"  OK - {llm.describe()} replied: {clean_text(reply, 60)}")
    except LLMError as exc:
        logger.error(f"LLM test failed: {exc}")
        ok = False
    logger.info("Testing research provider...")
    try:
        logger.info(f"  OK - {provider.name}: {test_research(provider)}")
    except ResearchError as exc:
        logger.error(f"Research test failed: {exc}")
        ok = False
    logger.info("")
    logger.info("All good - you can run: python main.py" if ok else "Fix the problem above, then run the test again.")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # never crash on unusual characters in a terminal
        except AttributeError:
            pass

    args = parse_args()
    fmt = (args.format or config.OUTPUT_FORMAT).strip().lower()
    input_path = resolve(args.input or config.INPUT_FILE)
    output_dir = resolve(config.OUTPUT_DIR)
    logger.register_secret(config.LLM_API_KEY)
    logger.register_secret(config.SEARCH_API_KEY)
    logger.set_log_file(output_dir / "run_log.txt")

    provider_label = PROVIDERS.get(config.LLM_PROVIDER.lower(), {}).get("label", config.LLM_PROVIDER)
    logger.banner("HR OUTREACH GENERATOR")
    logger.info("")
    logger.info(f"LLM Provider: {provider_label}")
    logger.info(f"Model: {config.LLM_MODEL}")
    logger.info(f"Research Provider: {config.SEARCH_PROVIDER}")
    logger.info(f"Input: {input_path.relative_to(PROJECT_DIR) if input_path.is_relative_to(PROJECT_DIR) else input_path}")
    logger.info(f"Output: {config.OUTPUT_DIR}/{BASE_NAME}.{fmt}")
    logger.info("")

    if fmt not in ("csv", "xlsx"):
        logger.error(f"OUTPUT_FORMAT must be 'csv' or 'xlsx' (got '{fmt}').")
        return 1
    try:
        llm = LLMManager(config.LLM_PROVIDER, config.LLM_API_KEY, config.LLM_MODEL, config.LLM_BASE_URL,
                         timeout=config.LLM_TIMEOUT_SECONDS, max_retries=config.MAX_RETRIES,
                         retry_base_delay=config.RETRY_BASE_DELAY_SECONDS,
                         min_interval=config.REQUEST_DELAY_SECONDS)
        provider = get_research_provider(config.SEARCH_PROVIDER, config.SEARCH_API_KEY,
                                         timeout=config.HTTP_TIMEOUT_SECONDS, retries=config.MAX_RETRIES,
                                         retry_base_delay=config.RETRY_BASE_DELAY_SECONDS)
    except (LLMError, ResearchError) as exc:
        logger.error(str(exc))
        return 1

    if args.test:
        return run_tests(llm, provider)

    if "<your" in config.EMAIL_SIGNATURE.lower():
        logger.warn("EMAIL_SIGNATURE in config.py still has <...> placeholders - fill in your links.")
    logger.info("Starting...")
    logger.info("")

    # ---------------------------------------------------------------- read input
    logger.info("Reading input file...")
    try:
        raw = read_emails(input_path)
    except InputFileError as exc:
        logger.error(str(exc))
        return 1
    valid, invalid, duplicates = validate_and_dedupe(raw)
    logger.info(f"Found {len(raw)} email addresses.")
    if invalid:
        logger.warn(f"{len(invalid)} invalid address(es) skipped: {', '.join(invalid[:5])}"
                    + (" ..." if len(invalid) > 5 else ""))
    logger.info(f"After deduplication: {len(valid)} emails.")
    if args.limit and args.limit > 0:
        valid = valid[:args.limit]
        logger.info(f"--limit {args.limit}: processing {len(valid)} emails.")
    if not valid:
        logger.error("No valid email addresses to process.")
        return 1

    rows: List[Dict[str, str]] = [
        make_row(bad, None, None, provider_label, llm.model, "failed", "invalid email address") for bad in invalid]
    groups = group_by_domain(valid)
    researcher = CompanyResearcher(
        provider, llm, use_llm=config.USE_LLM_FOR_RESEARCH_SUMMARY,
        cache_file=None if (args.no_cache or not config.CACHE_RESEARCH) else resolve(config.CACHE_FILE),
        cache_max_age_days=config.CACHE_MAX_AGE_DAYS, max_chars=config.MAX_RESEARCH_CHARS)

    profiles: Dict[str, CompanyProfile] = {}
    stop_reason = ""
    counts = {"success": 0, "partial": 0, "failed": len(invalid)}
    processed = set()

    try:
        # --------------------------------------------------- research (once per domain)
        logger.info("")
        logger.info(f"Researching companies... ({len(groups)} unique domain(s))")
        for i, domain in enumerate(groups, 1):
            try:
                profile = researcher.research(domain)
            except ResearchAuthError as exc:
                logger.error(str(exc))
                stop_reason = "search API key problem - fix SEARCH_API_KEY or set SEARCH_PROVIDER=website"
                break
            except FATAL_LLM_ERRORS as exc:
                logger.error(str(exc))
                stop_reason = f"LLM problem: {exc}"
                break
            except Exception as exc:  # one bad company must not stop the run
                profile = CompanyProfile(domain=domain, status="unavailable", note=f"research error: {exc}")
            profiles[domain] = profile
            if profile.status == "personal_domain":
                logger.progress(i, len(groups), f"{domain} - personal email provider, company unknown")
            elif profile.usable:
                extra = " (cached)" if profile.from_cache else ""
                logger.progress(i, len(groups), f"{domain} -> {profile.company_name}{extra}")
            else:
                logger.progress(i, len(groups), f"failed - {domain}")
                logger.info(f"       Reason: {profile.note}")
                logger.info("       Continuing...")

        # ----------------------------------------------------- generate one per contact
        logger.info("")
        logger.info("Generating personalized emails...")
        rate_limit_streak = 0
        for i, email in enumerate(valid, 1):
            name = guess_name(email)
            profile = profiles.get(get_domain(email))
            processed.add(email)

            def fail(reason: str) -> None:
                rows.append(make_row(email, name, profile, provider_label, llm.model, "failed", reason))
                counts["failed"] += 1
                logger.progress(i, len(valid), f"failed - {email}")
                logger.info(f"       Reason: {reason}")

            if stop_reason:
                fail(f"not processed: {stop_reason}")
                continue
            if profile is None:
                fail("company was not researched")
                continue
            if not profile.usable and not config.GENERATE_WITHOUT_RESEARCH:
                fail(profile.note or "research unavailable")
                logger.info("       Continuing...")
                continue
            try:
                result = generate_email(llm, config.EMAIL_PROMPT_TEMPLATE, name, profile,
                                        max_words=config.MAX_EMAIL_LENGTH, temperature=config.TEMPERATURE,
                                        signature=config.EMAIL_SIGNATURE)
            except FATAL_LLM_ERRORS as exc:
                logger.error(str(exc))
                stop_reason = f"LLM problem: {exc}"
                fail(stop_reason)
                continue
            except LLMRateLimitError as exc:
                rate_limit_streak += 1
                fail(f"rate limit: {exc}")
                if rate_limit_streak >= 3:
                    stop_reason = "the LLM kept rate-limiting - wait a while and run again (research is cached)"
                    logger.error(stop_reason)
                continue
            except LLMError as exc:
                fail(str(exc))
                logger.info("       Continuing...")
                continue
            except Exception as exc:
                fail(f"unexpected error: {exc}")
                logger.info("       Continuing...")
                continue

            rate_limit_streak = 0
            status = "success" if profile.usable else "partial"
            notes = [n for n in (result.note, profile.note) if n]
            rows.append(make_row(email, name, profile, provider_label, llm.model, status, "; ".join(notes),
                                 result.subject, result.body, result.company_interest))
            counts[status] += 1
            company = profile.company_name if profile.usable else "company unknown"
            logger.progress(i, len(valid), f"{name.name} - {company}")
    except KeyboardInterrupt:
        logger.warn("Stopped by you (Ctrl+C) - saving what's done so far.")
        for email in valid:
            if email not in processed:
                rows.append(make_row(email, guess_name(email), profiles.get(get_domain(email)), provider_label,
                                     llm.model, "failed", "not processed: stopped by user"))
                counts["failed"] += 1

    # ---------------------------------------------------------------------- export
    logger.info("")
    logger.info("Creating output file...")
    try:
        written = export(rows, output_dir, fmt, config.ALSO_WRITE_TXT)
    except OSError as exc:
        logger.error(f"Could not write the output file: {exc}")
        return 1

    logger.info("")
    logger.info("Done!")
    logger.info("")
    logger.info(f"Success: {counts['success']}   Partial: {counts['partial']}   Failed: {counts['failed']}")
    logger.info(f"LLM calls: {llm.calls}   Searches: {researcher.stats['searches']}   "
                f"Companies from cache: {researcher.stats['cache_hits']}")
    logger.info("")
    logger.info("Output:")
    for path in written:
        logger.info(str(path.relative_to(PROJECT_DIR) if path.is_relative_to(PROJECT_DIR) else path))
    logger.info("")
    logger.info("Nothing was sent. Review every draft (especially guessed names) before sending it yourself.")
    if stop_reason:
        logger.warn(f"The run stopped early: {stop_reason}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
