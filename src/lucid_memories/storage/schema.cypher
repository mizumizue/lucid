-- Map: 指示 / 処理 / 資料 / 話題 / 文脈。本文は SQLite。
-- 辺は意味（話題・文脈・関連）。Agent は生 Cypher を書かない。

CREATE NODE TABLE Directive (
  id STRING PRIMARY KEY,
  title STRING,
  normalized STRING,
  workspace_root STRING
);

CREATE NODE TABLE Procedure (
  id STRING PRIMARY KEY,
  title STRING,
  kind STRING,
  pointer STRING,
  workspace_root STRING
);

CREATE NODE TABLE Material (
  id STRING PRIMARY KEY,
  title STRING,
  kind STRING,
  pointer_uri STRING,
  knowledge_id STRING,
  workspace_root STRING
);

CREATE NODE TABLE Topic (
  id STRING PRIMARY KEY,
  title STRING,
  normalized STRING,
  workspace_root STRING
);

CREATE NODE TABLE Context (
  id STRING PRIMARY KEY,
  title STRING,
  normalized STRING,
  workspace_root STRING
);

CREATE REL TABLE TRIGGERS (
  FROM Directive TO Procedure,
  count INT64,
  first_at STRING,
  last_at STRING,
  status STRING,
  source STRING,
  sense STRING,
  label STRING
);

CREATE REL TABLE USES (
  FROM Procedure TO Material,
  count INT64,
  first_at STRING,
  last_at STRING,
  status STRING,
  source STRING,
  role STRING,
  sense STRING,
  label STRING
);

CREATE REL TABLE ABOUT (
  FROM Directive TO Topic,
  FROM Procedure TO Topic,
  FROM Material TO Topic,
  count INT64,
  first_at STRING,
  last_at STRING,
  status STRING,
  source STRING,
  label STRING
);

CREATE REL TABLE IN_CONTEXT (
  FROM Directive TO Context,
  FROM Procedure TO Context,
  FROM Material TO Context,
  count INT64,
  first_at STRING,
  last_at STRING,
  status STRING,
  source STRING,
  label STRING
);

CREATE REL TABLE RELATED (
  FROM Directive TO Directive,
  FROM Directive TO Procedure,
  FROM Directive TO Material,
  FROM Procedure TO Procedure,
  FROM Procedure TO Material,
  FROM Material TO Material,
  FROM Topic TO Topic,
  FROM Context TO Context,
  FROM Topic TO Context,
  sense STRING,
  label STRING,
  count INT64,
  first_at STRING,
  last_at STRING,
  status STRING,
  source STRING
);

CREATE REL TABLE FORBIDS (
  FROM Directive TO Procedure,
  FROM Directive TO Material,
  FROM Procedure TO Material,
  FROM Directive TO Directive,
  FROM Procedure TO Procedure,
  FROM Material TO Material,
  FROM Directive TO Topic,
  FROM Procedure TO Topic,
  FROM Material TO Topic,
  FROM Directive TO Context,
  FROM Procedure TO Context,
  FROM Material TO Context,
  reason STRING,
  created_at STRING,
  created_by STRING
);

CREATE REL TABLE KIND_OF (
  FROM Directive TO Directive,
  FROM Procedure TO Procedure,
  FROM Topic TO Topic,
  FROM Context TO Context
);

CREATE REL TABLE ALIAS_OF (
  FROM Directive TO Directive,
  FROM Topic TO Topic,
  FROM Context TO Context
);
