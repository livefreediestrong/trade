from narrative_checks import concerns, checked_report


def test_real_weekly_reply_cannot_turn_empty_journal_into_no_trades():
    evidence = {'outcomes': {'today': {'outcomes': 12, 'mean_net_bps': -27.38}}}
    for text in ['Zero live or broker paper executions occurred.', 'No actual or paper broker orders were executed during the week.', 'Four sessions recorded practice observations. Zero live or broker paper executions occurred.']:
        assert any('incomplete broker journal' in item for item in concerns(text, evidence))
    assert not concerns('No broker executions were recorded in the retained journal. Coverage is incomplete.', evidence)
    report = {'evidence': evidence, 'models': {'fox': {'result': {'summary': 'Review', 'findings': [
        {'text': 'No actual or paper broker orders were executed during the week.', 'evidence_ids': ['executions']}
    ]}}}}
    checked = checked_report(report)['models']['fox']
    assert not checked['checked_result']['findings'] and checked['checks']['flagged']
    assert report['models']['fox']['result']['findings'], 'Original model text remains a dated record'
