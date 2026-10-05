# JobTeaser integration investigation

Checked on 2026-10-05 using the terminal.

## Verified findings

- Public search: `https://www.jobteaser.com/fr/job-offers?keyword=devops` returns HTTP 403 and a Security checkup page to HTTPX.
- A standard JavaScript-enabled headless Edge session also returns HTTP 403 and a security challenge. Enabling JavaScript alone does not resolve access. No stealth plugins, CAPTCHA bypass or proxy rotation were used.
- The public keyword parameter is not verified, because no search results were accessible.
- JobTeaser advertises integrations for publishing recruiter offers, not a publicly documented candidate search API we can currently use.
- The app therefore retains its explicit unavailable status. These tests do not establish whether a user's normal signed-in session or school Career Center can access listings reliably.

## Next steps

1. Identify whether the user accesses JobTeaser directly or through a school Career Center, and obtain the Career Center URL when applicable.
2. Verify normal user access before implementing a session-backed integration. Authentication or security challenges require the user's own interaction; do not collect passwords in chat.
3. If native email job alerts are available in that account, consider importing them into the existing deduplication, recruiter-ban and Telegram pipeline. This depends on JobTeaser's email schedule and cannot promise immediate detection.
4. An approved candidate feed/API would be preferable for dependable rapid polling. Employer posting integrations alone do not satisfy this requirement.

Official recruiter integration information: https://recruiter.jobteaser.com/en/join-jobteaser/?flow=job
