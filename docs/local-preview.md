# Local preview login

For development on your own computer, set `ALLOW_DEVELOPMENT_LOGIN=true` in the project `.env`, keep `APP_ENV=development`, and restart the backend. The login page displays **Skip login**. The current local installation has this enabled.

The button provisions a dedicated Local Preview engineer account in its own Local Preview organization and creates a normal, expiring HttpOnly session. On an installation upgraded from the single-workspace release, an existing preview account stays in the default organization with the other existing accounts. Dashboard and tools use authenticated backend APIs; administrator account management remains restricted. Signing out revokes the session. Your normal sign-in account remains available.

Only browser origins on localhost or loopback addresses are accepted. The setting defaults to false, is unavailable outside development, and causes production startup to fail if enabled. Set `ALLOW_DEVELOPMENT_LOGIN=false` before deploying. This mode is intended for a trusted local computer.
