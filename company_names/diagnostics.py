"""Safe storage diagnostics for the app and server logs."""

import logging
import re


logger = logging.getLogger(__name__)


class RepositoryUnavailableError(RuntimeError):
    """Alias storage could not complete an operation."""


def storage_failure(message, *, backend, operation, stage, location,
                    error=None, row=None, field=None):
    """Describe a failure without logging raw exceptions, responses, or values.

    Context strings are developer-defined labels, never data from the server.
    Exception metadata is limited to class names, standard database error codes,
    and numeric HTTP status codes. Exception messages are never included.
    """
    context = [f"backend={backend}", f"operation={operation}", f"stage={stage}",
               f"code={location}"]
    if error is not None:
        context.append(f"error={type(error).__name__}")
        code = getattr(error, "code", None)
        if isinstance(code, str) and re.fullmatch(r"PGRST[0-9]{3}|[0-9]{2}[0-9A-Z]{3}", code):
            context.append(f"error_code={code}")
        status = getattr(getattr(error, "response", None), "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            context.append(f"http_status={status}")
    if row is not None:
        context.append(f"row={row}")
    if field is not None:
        context.append(f"field={field}")
    message = f"{message} [{'; '.join(context)}]"
    if backend == "supabase":
        message += (
            " Legacy Supabase storage is active. For the home server, configure"
            " ALIAS_API_URL and ALIAS_API_TOKEN as top-level Streamlit secrets,"
            " reboot the app, and process the PDFs again."
        )
    logger.error("%s", message)
    return RepositoryUnavailableError(message)
