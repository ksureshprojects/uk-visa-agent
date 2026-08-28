"""Formats a completed Phase 2 draft package (the dict shape returned by
app/workflow/assembly.py's AssemblyEngine.build_package) into an email
subject/body. Pure formatting only, no I/O — kept separate from
app/identity/session_manager.py (which owns actually sending it) so the
formatting itself is testable without a DB or a real mail server.
"""


def _label(field_name: str) -> str:
    return field_name.replace("_", " ").capitalize()


def format_package_email(case_id: str, package: dict) -> tuple[str, str]:
    visa_type = package["visa_type"]
    fields = package["fields"]
    documents = package["documents_confirmed"]

    lines = [
        f"Here's a summary of the {visa_type} application details you've provided for case {case_id}.",
        "",
        "Details:",
    ]
    for name, value in fields.items():
        lines.append(f"- {_label(name)}: {value}")

    lines.append("")
    lines.append("Documents confirmed ready:")
    if documents:
        for name in documents:
            lines.append(f"- {_label(name)}")
    else:
        lines.append("(none)")

    lines.append("")
    lines.append(
        "This is a draft only — a caseworker will review everything above before anything is submitted."
    )

    subject = f"Your {visa_type} application summary — case {case_id}"
    body = "\n".join(lines)
    return subject, body
