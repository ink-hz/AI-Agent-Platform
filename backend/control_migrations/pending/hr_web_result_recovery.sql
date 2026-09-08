-- UNNUMBERED DRAFT: owned disposable databases only; no production activation.
-- Link publication and late native evidence to the one existing command binding.
alter table platform_control.direct_command_bindings
  add column progress_source_seq bigint not null default 0 check (progress_source_seq >= 0),
  add column terminal_source_seq bigint check (terminal_source_seq > 0),
  add column published_message_id uuid references platform_control.conversation_messages(message_id),
  add column executor_stop_proof_ref text check (length(executor_stop_proof_ref) between 1 and 256),
  add column recovery_observed_at timestamptz,
  add column recovery_reason text check (length(recovery_reason) between 1 and 128),
  add column recovery_poll_after timestamptz,
  add constraint direct_result_requires_source check (published_message_id is null or terminal_source_seq is not null);
