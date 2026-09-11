"""Frozen v6 intent excludes renewable credentials, preserving its original hash."""
import hashlib
from pydantic import Field, create_model
from .contracts_v5 import canonical_command_bytes
from .contracts_v6 import CoreChatCommandV6, V6ContractError, _strict_wire
from .frozen_command_v5 import _fields, _CONFIG, _Attachment, _Output, _parse_frozen

_TemplateV6=create_model('FrozenCommandTemplateV6', __config__=_CONFIG,
    **_fields(CoreChatCommandV6,excluded={'leaseEpoch','eventCallbackUrl','commandHash',
        'inputAttachmentGrants','outputWriteGrant','businessToolGrant'}),
    input_attachments=(tuple[_Attachment,...],Field(alias='inputAttachments',max_length=32)),
    output_scope=(_Output|None,Field(alias='outputScope')))


def parse_frozen_v6(value):
    frozen=_parse_frozen(value,_TemplateV6)
    model=_TemplateV6.model_validate_json(canonical_command_bytes(value),strict=True)
    _strict_wire(value,model)
    expected=hashlib.sha256(canonical_command_bytes({key:value[key] for key in
        ('prompt','scope','rolePackage','methodSelection')})).hexdigest()
    if (len(set(model.tool_capabilities)) != len(model.tool_capabilities)
        or (model.tool_capabilities and model.permission_scope.tool_policy != 'default')
        or (model.method_selection and model.method_selection.catalog_release != model.role_package.catalog_release)):
        raise V6ContractError('v6 frozen capabilities invalid')
    if value['contextHash']!=expected or not set(g['attachmentId'] for g in value['inputAttachments']).issubset(value['scope']['attachmentIds']):
        raise V6ContractError('v6 frozen scope invalid')
    return frozen
