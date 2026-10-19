"""Visitor-facing assistant (Phase 11): website guide, form assistant, learning support.

Everything here treats visitor messages and the tenant's page content as untrusted data, never as
instructions. None of it calls an AI model: answers are quoted from the tenant's own content or
computed exactly, and the API says so.
"""
