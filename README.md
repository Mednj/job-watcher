**Job Watcher — discover opportunities early, apply faster**

A private, multi-account job monitoring app for the French market, inspired by our PS5 Deal Watcher. Each user has separate searches, job history, recruiter blocks, and Telegram bot settings. It continuously checks recruitment platforms for new opportunities and sends an alert through that user's bot, helping them apply while a listing is still fresh.

**Speed is the core priority.** The app should minimize the time between a job becoming visible on a monitored platform and the notification reaching you. Alerts go out as soon as a matching listing is detected, without waiting for a scheduled digest. Being first is the ambition; actual detection speed depends on how quickly each platform exposes new listings and how frequently it can reliably be checked.

The first version will focus on **LinkedIn and HelloWork**, with:

- Configurable searches by job title, keywords, location, contract type, and experience level.
- Continuous monitoring through available feeds, integrations, or scraping where feasible.
- Telegram alerts containing the job title, company, location, contract type, source, and direct application link.
- Duplicate detection to avoid repeatedly notifying you about the same opportunity.
- A simple dashboard showing recent matches, monitoring health, and the last successful check for each source.
- Measurements of detection and Telegram delivery times so speed can be verified and improved.

Coverage will expand gradually to APEC, Glassdoor, ICT Job Luxembourg, Indeed, JobTeaser, Monster, Welcome to the Jungle, Seekube, Pôle emploi, and relevant Campus Relations and Open Event sources.

The development approach is to make the first two sources fast and dependable, then add platforms one at a time. Each new integration should preserve the app’s central promise: **find relevant jobs early and get the alert to you immediately after detection.**
