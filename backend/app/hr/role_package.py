"""Read the deployed immutable Team role manifest; never accept a model path."""
import hashlib
import json
from pathlib import Path, PurePosixPath
from app.execution_relay.contracts_v6 import HrRolePackageRef


class HrRolePackages:
    def __init__(self, root, active_commit):
        self.root=Path(root)
        self.active_commit=active_commit

    def select(self, method_selection=None):
        commit=method_selection.catalog_release if method_selection is not None else self.active_commit
        import re
        if not isinstance(commit,str) or re.fullmatch('[a-f0-9]{40}',commit) is None:
            raise ValueError('HR role release unavailable')
        root=self.root/commit
        manifest_path=root/'role-package.json'
        if self.root.is_symlink() or root.is_symlink() or manifest_path.is_symlink():
            raise ValueError('HR role release unavailable')
        raw=manifest_path.read_bytes()
        manifest=json.loads(raw)
        if manifest.get('format')!='hr-role-package-v1' or manifest.get('teamCommit')!=commit or manifest.get('cwd')!='bots/hr':
            raise ValueError('HR role release invalid')
        identities=set()
        for entry in manifest['files']:
            name=entry['path']; relative=PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or str(relative)!=name or name in identities:
                raise ValueError('HR role file invalid')
            identities.add(name)
            if any(root.joinpath(*relative.parts[:i]).is_symlink() for i in range(1,len(relative.parts)+1)):
                raise ValueError('HR role file invalid')
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=entry['sha256']:
                raise ValueError('HR role content changed')
        if not {'bots/hr/CLAUDE.md','shared/base-rules.md','shared/orbbec-context.md','shared/web-research.md'}.issubset(identities):
            raise ValueError('HR role incomplete')
        return HrRolePackageRef(teamCommit=commit,catalogRelease=manifest['catalogRelease'],manifestSha256=hashlib.sha256(raw).hexdigest())
