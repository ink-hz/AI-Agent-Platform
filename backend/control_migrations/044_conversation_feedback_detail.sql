alter table platform_control.conversation_feedback
  add column reason text,
  add column comment_ciphertext bytea,
  add column comment_key_version integer;

alter table platform_control.conversation_feedback
  add constraint conversation_feedback_reason_v44 check (
    reason is null or reason in ('inaccurate','incomplete','unclear','unresolved','other')
  ),
  add constraint conversation_feedback_comment_pair_v44 check (
    (comment_ciphertext is null) = (comment_key_version is null)
  ),
  add constraint conversation_feedback_detail_v44 check (
    (rating = 'helpful' and reason is null and comment_ciphertext is null)
    or (rating = 'unhelpful' and reason is not null)
  );

comment on column platform_control.conversation_feedback.comment_ciphertext is
  'Encrypted optional member improvement comment; never exposed by member APIs.';
