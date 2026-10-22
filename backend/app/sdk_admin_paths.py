"""Exact SDK portal shell paths shared by login and identity middleware."""

import re

SDK_ADMIN_SHELL = re.compile(
    r"/sdk-admin(?:/?|/(?:products|delivery|diagnostics|knowledge)/?"
    r"|/knowledge/[A-Za-z0-9][A-Za-z0-9._-]{0,159})\Z"
)
