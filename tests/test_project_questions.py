from engine.project import Project


def test_questions_read_plain_and_legacy_formats_without_rewriting(tmp_path):
    research = tmp_path / 'research'
    research.mkdir()
    path = research / 'questions.md'
    original = (
        '# Questions\n'
        '- RQ1: What explains adoption?\n'
        '* RQ2: Какие есть ограничения? | keywords: adoption, limits\n'
        '- RQ3: Empty legacy tags? | keywords: \n'
    )
    path.write_text(original, encoding='utf-8')

    assert Project(tmp_path).questions() == [
        {'id': 'RQ1', 'question': 'What explains adoption?'},
        {'id': 'RQ2', 'question': 'Какие есть ограничения?'},
        {'id': 'RQ3', 'question': 'Empty legacy tags?'},
    ]
    assert path.read_text(encoding='utf-8') == original
