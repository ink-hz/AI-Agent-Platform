-- Adds only scoped proxy credentials to the existing durable callback registration.
alter table execution_worker.v5_callback_runs
 add column core_contract_version text not null default 'core_chat_collaboration_v5'
   check(core_contract_version in ('core_chat_collaboration_v5','core_chat_collaboration_v6')),
 add column business_grant_id uuid,
 add column business_token_hash bytea check(business_token_hash is null or octet_length(business_token_hash)=32);
