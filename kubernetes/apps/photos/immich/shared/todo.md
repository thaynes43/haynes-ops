I hit a problem documented in [issue 7289](https://github.com/immich-app/immich/issues/7289) and these steps fixed it...

```sh
thaynes@SYM-JNNQRV3:~$ k -n database exec -it postgres16-pgvecto-1 -- sh
Defaulted container "postgres" out of: postgres, bootstrap-controller (init), k8tz (init)
$ psql -U postgres -d immich
psql (16.2 (Debian 16.2-1.pgdg110+2))
Type "help" for help.

immich=# ALTER DATABASE immich SET search_path TO immich, public, vectors;
ALTER DATABASE
immich=# GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA vectors TO thaynes;
GRANT
immich=# GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO thaynes;
GRANT
immich=# ALTER DATABASE immich SET search_path TO immich, public, vectors;
ALTER DATABASE
immich=# GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA vectors TO postgres;
GRANT
immich=# GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
```