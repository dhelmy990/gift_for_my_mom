import pandas as pd
import pytest

from company_names import cleaning
from company_names.repository import AliasMapping
from company_names.service import prepare_aliases, save_alias_changes, ServiceValidationError


class Repository:
    def __init__(self, aliases=()):
        self.aliases = list(aliases)
        self.saved = []

    def list_aliases(self):
        return list(self.aliases)

    def upsert_aliases(self, mappings):
        self.saved.extend(mappings)
        by_key = {item.alias_key: item for item in self.aliases}
        by_key.update({item.alias_key: item for item in mappings})
        self.aliases = list(by_key.values())


@pytest.mark.parametrize('raw, expected', [
    ('  Xiang long Holidays  ', 'XIANG LONG HOLIDAYS'),
    ('Acme\t\n  Travel\u00a0Group', 'ACME TRAVEL GROUP'),
    ('Ａｃｍｅ　Ｔｒａｖｅｌ', 'ACME TRAVEL'),
    ('\ufeffAc\u200bme\u2060', 'ACME'),
    ('Cafe\u0301 “Tours” – O’Neil', 'CAFÉ "TOURS" - O\'NEIL'),
    ('Straße & Co-op', 'STRASSE & CO-OP'),
    ('旅行社 123', '旅行社 123'),
])
def test_formatting_is_canonical_and_idempotent(raw, expected):
    assert hasattr(cleaning, 'normalize_company_text')
    assert cleaning.normalize_company_text(raw) == expected
    assert cleaning.normalize_company_text(expected) == expected


@pytest.mark.parametrize('raw', ['', ' \u200b ', '---', 'ACME\x00', 'ACME\u202e', 'A' * 2001, None])
def test_unusable_names_are_rejected(raw):
    assert hasattr(cleaning, 'normalize_company_text')
    with pytest.raises(ValueError):
        cleaning.normalize_company_text(raw)


def test_cleanup_cannot_leave_a_punctuation_only_name():
    with pytest.raises(ValueError, match="no letters or numbers"):
        cleaning.clean_company_name("& (FORMERLY ACME)")


def test_legacy_saved_aliases_coalesce_after_unicode_formatting():
    repository = Repository([
        AliasMapping('Ａｃｍｅ', 'ａｃｍｅ', '  One\u00a0Company'),
        AliasMapping('ACME', 'acme', 'ONE COMPANY'),
    ])
    prepared = prepare_aliases(pd.DataFrame([
        {'agent_name': 'Acme', 'rns': 1, 'revenue': 1},
    ]), repository)
    assert prepared.review_rows[0].final_name == 'ONE COMPANY'
    assert prepared.review_rows[0].status == 'saved'


def test_genuine_conflict_in_legacy_saved_aliases_is_actionable():
    repository = Repository([
        AliasMapping('Ａｃｍｅ', 'ａｃｍｅ', 'First'),
        AliasMapping('ACME', 'acme', 'Second'),
    ])
    with pytest.raises(ServiceValidationError, match="key 'acme': 'FIRST' and 'SECOND'"):
        prepare_aliases(pd.DataFrame([
            {'agent_name': 'Acme', 'rns': 1, 'revenue': 1},
        ]), repository)
    assert repository.saved == []


@pytest.mark.parametrize('aliases', [
    [AliasMapping('Ａｃｍｅ', 'ａｃｍｅ', 'Old Destination')],
    [AliasMapping('Cafe\u0301', 'cafe', 'Old Destination')],
    [AliasMapping('Ａｃｍｅ', 'ａｃｍｅ', 'Old Destination'),
     AliasMapping('ACME', 'acme', 'Old Destination')],
])
def test_editing_legacy_unicode_alias_can_be_saved_and_reloaded(aliases):
    repository = Repository(aliases)
    rows = pd.DataFrame([
        {'agent_name': aliases[0].cleaned_alias, 'rns': 1, 'revenue': 10},
    ])
    prepared = prepare_aliases(rows, repository)
    source = prepared.review_rows[0].cleaned_name
    save_alias_changes(prepared, {source: 'NEW DESTINATION'}, repository)

    reloaded = prepare_aliases(rows, repository)
    assert reloaded.review_rows[0].final_name == 'NEW DESTINATION'
    assert all(item.canonical_name == 'NEW DESTINATION' for item in repository.aliases)
    assert {item.alias_key for item in aliases} <= {item.alias_key for item in repository.saved}


def test_new_mapping_can_reuse_legacy_key_without_overwriting_distinct_name():
    repository = Repository([AliasMapping('Cafe\u0301', 'cafe', 'OLD')])
    rows = pd.DataFrame([
        {'agent_name': 'CAFÉ', 'rns': 1, 'revenue': 10},
        {'agent_name': 'CAFE', 'rns': 2, 'revenue': 20},
    ])
    prepared = prepare_aliases(rows, repository)
    save_alias_changes(prepared, {'CAFÉ': 'ACCENTED', 'CAFE': 'UNACCENTED'}, repository)
    reloaded = prepare_aliases(rows, repository)
    assert {row.cleaned_name: row.final_name for row in reloaded.review_rows} == {
        'CAFÉ': 'ACCENTED', 'CAFE': 'UNACCENTED',
    }


def test_partial_report_preserves_displaced_legacy_company():
    repository = Repository([AliasMapping('Cafe\u0301', 'cafe', 'ACCENTED')])
    rows = pd.DataFrame([{'agent_name': 'CAFE', 'rns': 2, 'revenue': 20}])
    prepared = prepare_aliases(rows, repository)
    save_alias_changes(prepared, {'CAFE': 'UNACCENTED'}, repository)

    reloaded = prepare_aliases(pd.DataFrame([
        {'agent_name': 'CAFÉ', 'rns': 1, 'revenue': 10},
        {'agent_name': 'CAFE', 'rns': 2, 'revenue': 20},
    ]), repository)
    assert {row.cleaned_name: row.final_name for row in reloaded.review_rows} == {
        'CAFÉ': 'ACCENTED', 'CAFE': 'UNACCENTED',
    }
    assert all(row.status == 'saved' for row in reloaded.review_rows)


def test_chained_unicode_displacement_preserves_each_company():
    repository = Repository([
        AliasMapping('A\u030a', 'a', 'RING DESTINATION'),
        AliasMapping('Å\u0301', 'å', 'ACUTE DESTINATION'),
    ])
    rows = pd.DataFrame([
        {'agent_name': 'A', 'rns': 1, 'revenue': 10},
        {'agent_name': 'Ǻ', 'rns': 2, 'revenue': 20},
    ])
    prepared = prepare_aliases(rows, repository)
    save_alias_changes(prepared, {'A': 'PLAIN', 'Ǻ': 'NEW ACUTE'}, repository)
    reloaded = prepare_aliases(pd.concat([
        rows, pd.DataFrame([{'agent_name': 'Å', 'rns': 3, 'revenue': 30}]),
    ]), repository)
    assert {row.cleaned_name: row.final_name for row in reloaded.review_rows} == {
        'A': 'PLAIN', 'Ǻ': 'NEW ACUTE', 'Å': 'RING DESTINATION',
    }
    assert all(row.status == 'saved' for row in reloaded.review_rows)


def test_real_xiang_variants_save_immediately_and_preserve_totals():
    repository = Repository()
    frame = pd.DataFrame([
        {'agent_name': 'Xiang Long Holidays Pte', 'rns': 2, 'revenue': 100},
        {'agent_name': 'Xiang long Holidays Pte Ltd ', 'rns': 3, 'revenue': 200},
    ])
    prepared = prepare_aliases(frame, repository)
    result = save_alias_changes(prepared, {r.cleaned_name: r.final_name for r in prepared.review_rows}, repository)
    assert result.to_dict('records') == [{'TRAVEL AGENT': 'XIANG LONG HOLIDAYS',
                                         'Sum of RNS': 5.0, 'Sum of R REVENUE': 300.0}]
    assert len(repository.saved) == 1


def test_saved_and_suggested_targets_are_formatted_without_stripping_legal_name():
    repository = Repository([AliasMapping('HKTRM', 'hktrm', '  Hong Kong\u00a0TUYI Limited  ')])
    prepared = prepare_aliases(pd.DataFrame([
        {'agent_name': 'HKTRM', 'rns': 1, 'revenue': 1},
        {'agent_name': 'HKTRMs', 'rns': 1, 'revenue': 1},
    ]), repository)
    assert prepared.review_rows[0].final_name == 'HONG KONG TUYI LIMITED'
    assert prepared.review_rows[1].suggestion.canonical_name == 'HONG KONG TUYI LIMITED'


@pytest.mark.parametrize('bad', ['Acme', 'ACME ', ' ACME', 'ACME  TRAVEL', 'ACME\u00a0TRAVEL',
                                 'AC\u200bME', 'ＡＣＭＥ', 'ACME\nTRAVEL', 'ACME—TRAVEL', '---'])
def test_save_rejects_reintroduced_formatting_errors(bad):
    repository = Repository()
    prepared = prepare_aliases(pd.DataFrame([{'agent_name': 'ACME', 'rns': 1, 'revenue': 1}]), repository)
    with pytest.raises(ServiceValidationError, match='ACME'):
        save_alias_changes(prepared, {'ACME': bad}, repository)
    assert repository.saved == []


def test_punctuation_variants_with_same_existing_key_get_a_common_default():
    repository = Repository()
    prepared = prepare_aliases(pd.DataFrame([
        {'agent_name': 'ACME-TRAVEL', 'rns': 1, 'revenue': 10},
        {'agent_name': 'ACME TRAVEL', 'rns': 2, 'revenue': 20},
    ]), repository)
    assert len({row.final_name for row in prepared.review_rows}) == 1
    save_alias_changes(prepared, {r.cleaned_name: r.final_name for r in prepared.review_rows}, repository)
    assert len(repository.saved) == 1
