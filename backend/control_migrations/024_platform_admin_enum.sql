alter type platform_control.user_role
  add value if not exists 'platform_admin' before 'platform_owner';
