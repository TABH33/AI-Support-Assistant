"""Standalone, cron-schedulable maintenance jobs (as opposed to request-scoped
API/service code). Each job is a plain Python function plus a
`python -m app.jobs.<name>` entry point -- no scheduler library/framework is
introduced; an external scheduler (cron, a Kubernetes CronJob, Windows Task
Scheduler, etc.) is expected to invoke the module directly.
"""
