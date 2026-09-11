-- Preserve existing callback registrations; add the new wire version only.
alter table execution_worker.v5_callback_runs drop constraint v5_callback_runs_core_contract_version_check;
alter table execution_worker.v5_callback_runs add constraint v5_callback_runs_core_contract_version_check
 check(core_contract_version in ('core_chat_collaboration_v5','core_chat_collaboration_v6','core_chat_collaboration_v7'));
