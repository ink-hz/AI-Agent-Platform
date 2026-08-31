"""Shared read-only SQL predicates for Review ownership and audit integrity.

Legacy canonical rows without a valid two-sided event pair intentionally fail
closed. They require an explicit, independently verified operational repair;
read paths must not synthesize immutable historical audit events.
"""

CANONICAL_EVENT_PAIR_INVALID_SQL = """
(
  issue.canonical_issue_id is not null
  and not exists (
    select 1
    from platform_review.feedback_issue_events canonical_source
    join platform_review.feedback_issues canonical_issue
      on canonical_issue.id=issue.canonical_issue_id
     and canonical_issue.agent_id=issue.agent_id
    join platform_review.feedback_issue_events canonical_target
      on canonical_target.issue_id=issue.canonical_issue_id
     and canonical_target.event_type='issue_absorbed'
     and canonical_target.before->>'source_issue_id'=issue.id::text
     and canonical_target.after->>'target_issue_id'=issue.canonical_issue_id::text
     and canonical_target.actor is not distinct from canonical_source.actor
     and canonical_target.reason is not distinct from canonical_source.reason
    where canonical_source.issue_id=issue.id
      and canonical_source.event_type='issue_merged'
      and canonical_source.before->>'id'=issue.id::text
      and canonical_source.after->>'id'=issue.id::text
      and canonical_source.before->>'agent_id'=issue.agent_id
      and canonical_source.after->>'agent_id'=issue.agent_id
      and canonical_source.after->>'canonical_issue_id'=issue.canonical_issue_id::text
  )
)
or exists (
  select 1
  from platform_review.feedback_issue_events canonical_event
  where canonical_event.issue_id=issue.id
    and (
      (canonical_event.event_type='issue_merged' and (
        canonical_event.before->>'id' is distinct from issue.id::text
        or canonical_event.after->>'id' is distinct from issue.id::text
        or canonical_event.before->>'agent_id' is distinct from issue.agent_id
        or canonical_event.after->>'agent_id' is distinct from issue.agent_id
        or canonical_event.after->>'canonical_issue_id' is null
        or not exists (
          select 1
          from platform_review.feedback_issues canonical_issue
          join platform_review.feedback_issue_events canonical_target
            on canonical_target.issue_id=canonical_issue.id
           and canonical_target.event_type='issue_absorbed'
           and canonical_target.before->>'source_issue_id'=issue.id::text
           and canonical_target.after->>'target_issue_id'=canonical_issue.id::text
           and canonical_target.actor is not distinct from canonical_event.actor
           and canonical_target.reason is not distinct from canonical_event.reason
          where canonical_issue.id::text=
              canonical_event.after->>'canonical_issue_id'
            and canonical_issue.agent_id=issue.agent_id
        )
      ))
      or (canonical_event.event_type='issue_absorbed' and (
        canonical_event.before->>'source_issue_id' is null
        or canonical_event.after->>'target_issue_id'
          is distinct from issue.id::text
        or not exists (
          select 1
          from platform_review.feedback_issues canonical_issue
          join platform_review.feedback_issue_events canonical_source
            on canonical_source.issue_id=canonical_issue.id
           and canonical_source.event_type='issue_merged'
           and canonical_source.before->>'id'=canonical_issue.id::text
           and canonical_source.after->>'id'=canonical_issue.id::text
           and canonical_source.before->>'agent_id'=canonical_issue.agent_id
           and canonical_source.after->>'agent_id'=canonical_issue.agent_id
           and canonical_source.after->>'canonical_issue_id'=issue.id::text
           and canonical_source.actor is not distinct from canonical_event.actor
           and canonical_source.reason is not distinct from canonical_event.reason
          where canonical_issue.id::text=
              canonical_event.before->>'source_issue_id'
            and canonical_issue.agent_id=issue.agent_id
        )
      ))
    )
)
""".strip()

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
      or exists (
        select 1
        from jsonb_array_elements_text(
          coalesce(event.after->'source_feedback_keys', '[]'::jsonb)
        ) as event_feedback_key(feedback_key)
        left join platform_read.feedback event_feedback
          on event_feedback.feedback_key=event_feedback_key.feedback_key
         and event_feedback.agent_id=issue.agent_id
         and event_feedback.turn_key=event.after->>'source_turn_key'
        where event_feedback.feedback_key is null
      )
      or jsonb_array_length(
        coalesce(event.after->'source_feedback_keys', '[]'::jsonb)
      ) <> (
        select count(distinct feedback_key)
        from jsonb_array_elements_text(
          coalesce(event.after->'source_feedback_keys', '[]'::jsonb)
        ) as event_feedback_key(feedback_key)
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
      or (event.event_type in (
        'turn_linked', 'turn_linked_from_release_handoff'
      ) and historical_link.issue_id is distinct from issue.id
      and not exists (
        select 1 from platform_review.feedback_issue_events merge_move
        where merge_move.issue_id=issue.id
          and merge_move.event_type in ('link_moved_in', 'link_moved_out')
          and merge_move.before->>'id'=event.after->>'id'
          and merge_move.after->>'id'=event.after->>'id'
      ) and (
        issue.canonical_issue_id is null
        or not exists (
          select 1 from canonical_walk merge_walk
          where merge_walk.root_id=issue.id
            and merge_walk.current_id=historical_link.issue_id
            and not merge_walk.cycle
        )
        or not exists (
          select 1 from platform_review.feedback_issue_events merge_source
          where merge_source.issue_id=issue.id
            and merge_source.event_type='issue_merged'
            and merge_source.before->>'id'=issue.id::text
            and merge_source.after->>'id'=issue.id::text
            and merge_source.before->>'agent_id'=issue.agent_id
            and merge_source.after->>'agent_id'=issue.agent_id
            and merge_source.after->>'canonical_issue_id'
              =issue.canonical_issue_id::text
        )
        or not exists (
          select 1 from platform_review.feedback_issue_events merge_target
          where merge_target.issue_id=issue.canonical_issue_id
            and merge_target.event_type='issue_absorbed'
            and merge_target.before->>'source_issue_id'=issue.id::text
            and merge_target.after->>'target_issue_id'
              =issue.canonical_issue_id::text
        )
      ))
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
