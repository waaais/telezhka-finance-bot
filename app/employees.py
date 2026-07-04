import re

SPECIAL_EMPLOYEE_BUCKETS = {
    "ксюша": "КСЮША",
    "настя": "НАСТЯ",
    "кристина": "КРИСТИНА",
}
OTHER_EMPLOYEE_BUCKET = "&"


def normalize_employee_name(name: str) -> str:
    name = name.strip()
    if name == OTHER_EMPLOYEE_BUCKET:
        return OTHER_EMPLOYEE_BUCKET
    if not name:
        return name
    if name.islower():
        return name[:1].upper() + name[1:]
    return name


def normalize_employee_group(value: str) -> str:
    names = split_employee_group(value)
    return "+".join(names)


def split_employee_group(value: str) -> list[str]:
    normalized = (
        value.replace("＋", "+")
        .replace("&amp;", OTHER_EMPLOYEE_BUCKET)
        .replace(" и ", "+")
    )
    parts = [part.strip() for part in re.split(r"\s*\+\s*", normalized) if part.strip()]
    result: list[str] = []
    for part in parts:
        if part == OTHER_EMPLOYEE_BUCKET:
            result.append(OTHER_EMPLOYEE_BUCKET)
            continue

        words = re.findall(r"[А-ЯЁа-яёA-Za-z][а-яёa-zA-ZА-ЯЁ-]{1,40}", part)
        if words:
            result.append(normalize_employee_name(words[0]))
    return result


def employee_bucket(value: str) -> str:
    if value.strip() == OTHER_EMPLOYEE_BUCKET:
        return OTHER_EMPLOYEE_BUCKET
    return SPECIAL_EMPLOYEE_BUCKETS.get(value.strip().casefold(), OTHER_EMPLOYEE_BUCKET)
