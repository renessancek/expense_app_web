import csv
import datetime
import io
import json
import os
import re
import shutil
from app_paths import user_data_dir

_CATEGORY_HEADERS = {"category", "kategorie", "cat"}
_KEYWORD_HEADERS = {
    "keyword",
    "keywords",
    "stichwort",
    "stichworter",
    "schluesselwort",
    "schluesselworter",
}


def _read_import_text(path):
    with open(path, "rb") as handle:
        data = handle.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode the rules file.")


def _normalize_header(value):
    text = str(value or "").strip().lower()
    return (
        text.replace("ä", "a")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ß", "ss")
    )


def _detect_csv_delimiter(text):
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    counts = {";": first_line.count(";"), ",": first_line.count(","), "\t": first_line.count("\t")}
    if max(counts.values()) == 0:
        return ","
    return max(counts, key=counts.get)


def _split_keyword_cell(value):
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip().lower() for part in re.split(r"[,;]", text) if part.strip()]


def rules_from_csv(text):
    """Parse a Sheets-style rules CSV into the JSON rules list."""
    if not str(text or "").strip():
        raise ValueError("The CSV file is empty.")

    delimiter = _detect_csv_delimiter(text)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as error:
        raise ValueError(f"Could not parse the CSV file: {error}") from error

    rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("The CSV file is empty.")

    header = [_normalize_header(cell) for cell in rows[0]]
    has_header = header[0] in _CATEGORY_HEADERS if header else False
    data_rows = rows[1:] if has_header else rows

    if has_header:
        try:
            category_index = next(index for index, name in enumerate(header) if name in _CATEGORY_HEADERS)
        except StopIteration:
            category_index = 0
        keyword_indexes = [index for index, name in enumerate(header) if name in _KEYWORD_HEADERS]
        extra_indexes = [
            index
            for index in range(len(header))
            if index != category_index and index not in keyword_indexes
        ]
        keyword_indexes = keyword_indexes + extra_indexes
        if not keyword_indexes:
            keyword_indexes = list(range(category_index + 1, len(header)))
    else:
        category_index = 0
        keyword_indexes = None

    by_category = {}
    for row in data_rows:
        if category_index >= len(row):
            continue
        category = row[category_index].strip()
        if not category:
            continue
        if _normalize_header(category) in _CATEGORY_HEADERS:
            continue

        if keyword_indexes is None:
            cells = row[category_index + 1 :]
        else:
            cells = [row[index] for index in keyword_indexes if index < len(row)]
            if len(row) > len(header):
                cells.extend(row[len(header) :])

        keywords = []
        for cell in cells:
            for keyword in _split_keyword_cell(cell):
                if keyword not in keywords:
                    keywords.append(keyword)
        if not keywords:
            continue

        existing = by_category.setdefault(category, [])
        for keyword in keywords:
            if keyword not in existing:
                existing.append(keyword)

    if not by_category:
        raise ValueError(
            "The CSV file needs a category column and at least one keyword. "
            "Use headers like category,keywords or one keyword per extra column."
        )

    return [{"category": category, "keywords": keywords} for category, keywords in by_category.items()]

class Categorizer:
    def __init__(self, rules_path=None):
        if rules_path is None:
            self.rules_path = str(user_data_dir() / 'rules.json')
            self._migrate_legacy_rules()
        else:
            self.rules_path = rules_path

        self.load_rules()

    def _migrate_legacy_rules(self):
        if os.path.exists(self.rules_path):
            return

        here = os.path.dirname(os.path.abspath(__file__))
        for legacy_path in (
            os.path.join(here, 'rules.json'),
            os.path.join(os.path.dirname(here), 'rules.json'),
        ):
            if os.path.exists(legacy_path):
                os.makedirs(os.path.dirname(os.path.abspath(self.rules_path)), exist_ok=True)
                shutil.copy2(legacy_path, self.rules_path)
                return

    def load_rules(self):
        if os.path.exists(self.rules_path):
            with open(self.rules_path, 'r') as f:
                data = json.load(f)
                self.rules = data.get('rules', [])
        else:
            self.rules = []

        self._compile_regexes()

    def _compile_regexes(self):
        self._compiled_rules = []
        for rule in self.rules:
            compiled_keywords = [re.compile(rf'\b{re.escape(k.lower())}\b') for k in rule['keywords']]
            self._compiled_rules.append({
                'category': rule['category'],
                'compiled_keywords': compiled_keywords
            })

    def save_rules(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.rules_path)), exist_ok=True)

        # Create backup before saving
        if os.path.exists(self.rules_path):
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(self.rules_path)), 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(backup_dir, f"rules_backup_{timestamp}.json")
            
            shutil.copy2(self.rules_path, backup_path)
            
            # Keep only the last 10 backups to save space
            backups = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.startswith("rules_backup_")])
            if len(backups) > 10:
                for old_backup in backups[:-10]:
                    os.remove(old_backup)

        data = {
            'rules': self.rules
        }
        with open(self.rules_path, 'w') as f:
            json.dump(data, f, indent=4)

    def _persist_rules(self):
        self.save_rules()
        self._compile_regexes()

    def import_rules(self, data):
        """Validate and replace all global rules from imported JSON data."""
        if not isinstance(data, dict):
            raise ValueError("The file must contain a JSON object.")

        rules = data.get('rules')

        if not isinstance(rules, list):
            raise ValueError("The file must contain a 'rules' list.")

        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("Each rule must be an object.")
            category = rule.get('category')
            keywords = rule.get('keywords')
            if not isinstance(category, str) or not category.strip():
                raise ValueError("Each rule needs a non-empty text category.")
            if not isinstance(keywords, list) or not all(isinstance(keyword, str) for keyword in keywords):
                raise ValueError("Each rule needs a list of text keywords.")

        self.rules = [
            {
                'category': rule['category'].strip(),
                'keywords': [keyword.lower().strip() for keyword in rule['keywords'] if keyword.strip()]
            }
            for rule in rules
        ]
        self._persist_rules()

    def import_rules_from_path(self, path):
        """Replace all global rules from a JSON or CSV file."""
        raw = _read_import_text(path)
        stripped = raw.lstrip()
        suffix = os.path.splitext(path)[1].lower()
        looks_like_json = stripped.startswith("{") or stripped.startswith("[")
        if suffix == ".json" or looks_like_json:
            try:
                self.import_rules(json.loads(raw))
                return
            except json.JSONDecodeError:
                if looks_like_json:
                    raise
        self.import_rules({"rules": rules_from_csv(raw)})

    def suggest_category(self, description):
        desc = description.lower()

        # 1. First priority: Manual/Global Rules
        for compiled_rule in self._compiled_rules:
            for pattern in compiled_rule['compiled_keywords']:
                if pattern.search(desc):
                    return compiled_rule['category']

        return 'Sonstiges'

    def add_rule(self, keywords, category):
        # Check if rule with this category already exists and append keywords
        for rule in self.rules:
            if rule['category'] == category:
                rule['keywords'] = list(set(rule['keywords'] + keywords))
                self._persist_rules()
                return
        
        self.rules.append({"keywords": keywords, "category": category})
        self._persist_rules()

    def delete_rule(self, category):
        self.rules = [r for r in self.rules if r['category'] != category]
        self._persist_rules()

    def get_all_categories(self):
        # Unique set of categories from rules
        cats = {rule['category'] for rule in self.rules}
        
        # Ensure default essential categories exist
        defaults = {"Sonstiges", "Supermarkt", "Amazon", "Versicherung", "Computerspiele", "Trading", "Haus"}
        cats.update(defaults)
        
        return sorted(list(cats))

    def update_rule_keywords(self, category, keywords):
        for rule in self.rules:
            if rule['category'] == category:
                rule['keywords'] = [k.strip().lower() for k in keywords]
                self._persist_rules()
                return True
        return False

    def rename_category(self, old_name, new_name):
        """Rename ``old_name`` to ``new_name`` in global keyword rules.

        Categories are plain strings. If ``new_name`` already exists, merge
        keywords from ``old_name`` into that rule and drop the old rule
        (rename-as-merge). Empty/whitespace ``new_name`` raises ValueError.
        Returns True on success, False if ``old_name`` is missing or names
        are equal after strip.
        """
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("New category name must not be empty.")
        if not old_name or old_name == new_name:
            return False

        old_rule = None
        existing_target = None
        for rule in self.rules:
            if rule["category"] == old_name:
                old_rule = rule
            elif rule["category"] == new_name:
                existing_target = rule

        if old_rule is None:
            return False

        if existing_target is not None:
            merged = list(existing_target["keywords"])
            for keyword in old_rule["keywords"]:
                if keyword not in merged:
                    merged.append(keyword)
            existing_target["keywords"] = merged
            self.rules = [rule for rule in self.rules if rule is not old_rule]
        else:
            old_rule["category"] = new_name

        self._persist_rules()
        return True

    def restore_latest_backup(self):
        """Restores the most recent file from the backups directory."""
        backup_dir = os.path.join(os.path.dirname(os.path.abspath(self.rules_path)), 'backups')
        if not os.path.exists(backup_dir):
            return False, "No backups found."
            
        backups = sorted([os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.startswith("rules_backup_")])
        if not backups:
            return False, "No backups found."
            
        latest_backup = backups[-1]
        shutil.copy2(latest_backup, self.rules_path)
        self.load_rules()
        return True, f"Restored from {os.path.basename(latest_backup)}"
