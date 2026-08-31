"""Shared read-only SQL predicate for historical Review link ownership."""

HISTORICAL_LINK_EVENT_INVALID_SQL = """
exists (
  select 1
  from platform_review.feedback_issue_events event
  left join platform_review.feedback_issue_links historical_link
    on historical_link.id::text=event.after->>'id'
  left join platform_review.feedback_issues historical_link_issue
    on historical_link_issue.id=historical_link.issue_id
  left join platform_review.feedback_issues historical_before_issue
    on historical_before_issue.id::text=event.before->>'issue_id'
  left join platform_review.feedback_issues historical_after_issue
    on historical_after_issue.id::text=event.after->>'issue_id'
  where event.issue_id=issue.id
    and event.event_type in (
      'turn_linked', 'turn_linked_from_release_handoff',
      'link_moved_in', 'link_moved_out'
    )
    and (
      event.after->>'agent_id' is distinct from issue.agent_id
      or not exists (
        select 1 from platform_read.turns event_after_turn
        where event_after_turn.turn_key=event.after->>'source_turn_key'
          and event_after_turn.agent_id=issue.agent_id
          and (issue.agent_id<>'ai-fae-agent'
            or event_after_turn.source_kind='fae')
      )
      or historical_link.id is null
      or historical_link.agent_id is distinct from issue.agent_id
      or historical_link.source_turn_key
        is distinct from event.after->>'source_turn_key'
      or historical_link_issue.id is null
      or historical_link_issue.agent_id is distinct from issue.agent_id
      or (event.event_type in (
        'turn_linked', 'turn_linked_from_release_handoff'
      ) and event.after->>'issue_id' is distinct from issue.id::text)
      or (event.event_type in ('link_moved_in', 'link_moved_out') and (
        event.before->>'id' is distinct from event.after->>'id'
        or event.before->>'agent_id' is distinct from event.after->>'agent_id'
        or event.before->>'source_turn_key'
          is distinct from event.after->>'source_turn_key'
        or historical_before_issue.id is null
        or historical_before_issue.agent_id is distinct from issue.agent_id
        or historical_after_issue.id is null
        or historical_after_issue.agent_id is distinct from issue.agent_id
        or not exists (
          select 1 from platform_read.turns event_before_turn
          where event_before_turn.turn_key=event.before->>'source_turn_key'
            and event_before_turn.agent_id=issue.agent_id
            and (issue.agent_id<>'ai-fae-agent'
              or event_before_turn.source_kind='fae')
        )
        or (event.event_type='link_moved_out'
          and event.before->>'issue_id' is distinct from issue.id::text)
        or (event.event_type='link_moved_in'
          and event.after->>'issue_id' is distinct from issue.id::text)
      ))
    )
)
""".strip()
