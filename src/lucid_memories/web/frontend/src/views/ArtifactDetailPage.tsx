import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  DateCell,
  EmptyState,
  ExpandableText,
  Panel,
  PanelHeading,
  ResourceState,
  SessionLink,
  StatusBadge,
} from "../components";
import { contentAvailabilityLabel, formatBytes, storageScopeLabel } from "../format";
import { useRefresh, useResource } from "../hooks";
import type { Artifact } from "../types";

export function ArtifactDetailPage() {
  const { id = "" } = useParams();
  const { revision } = useRefresh();
  const resource = useResource((signal) => api.artifact(id, signal), [id, revision]);
  return (
    <div className="view">
      <div className="back-link">
        <Link to="/activity">← アクティビティ一覧に戻る</Link>
      </div>
      <ResourceState resource={resource}>
        {(artifact) => <ArtifactDetail artifact={artifact} />}
      </ResourceState>
    </div>
  );
}

function ArtifactDetail({ artifact }: { artifact: Artifact }) {
  return (
    <div className="detail-stack">
      <Panel>
        <div className="panel-kicker">ARTIFACT DETAIL</div>
        <h2>{artifact.relative_path || artifact.name || artifact.path || "無題の成果物"}</h2>
        <div className="detail-grid">
          <div>
            <label>保管場所</label>
            <p>{storageScopeLabel(artifact.storage_scope)}</p>
          </div>
          <div>
            <label>可用性</label>
            <p><StatusBadge value={contentAvailabilityLabel(artifact.content_availability)} /></p>
          </div>
          <div>
            <label>保管ポリシー</label>
            <p>{artifact.storage_policy || "—"}</p>
          </div>
          <div>
            <label>ファイルサイズ</label>
            <p className="mono">{artifact.size_bytes == null ? "—" : formatBytes(artifact.size_bytes)}</p>
          </div>
        </div>
        <p className="muted">{artifact.message}</p>
      </Panel>
      <div className="content-grid">
        <Panel>
          <PanelHeading kicker="ORIGIN" title="保管・永続化状態" />
          <div className="status-summary">
            <div className="status-summary-row"><span>生成元 / 起点</span><strong>{artifact.origin || "—"}</strong></div>
            <div className="status-summary-row"><span>Blob 永続化</span><strong>{artifact.blob_recorded ? "あり" : "なし"}</strong></div>
            <div className="status-summary-row"><span>実パスの存在</span><strong>{artifact.path_exists ? "あり" : "なし"}</strong></div>
            <div className="status-summary-row"><span>MIME タイプ</span><strong>{artifact.mime_type || "—"}</strong></div>
          </div>
        </Panel>
        <Panel>
          <PanelHeading kicker="SESSION" title="関連セッション" />
          <div className="status-summary">
            <div className="status-summary-row">
              <span>セッション</span>
              <strong><SessionLink id={artifact.conversation_id} title={artifact.session_title} /></strong>
            </div>
            <div className="status-summary-row"><span>最終更新</span><strong><DateCell value={artifact.updated_at} /></strong></div>
            <div className="status-summary-row"><span>コンテンツハッシュ</span><strong>{(artifact.blob_sha || artifact.sha256 || "").slice(0, 16) || "—"}</strong></div>
            <div className="status-summary-row"><span>種別</span><strong>{artifact.kind || artifact.source || "—"}</strong></div>
          </div>
        </Panel>
      </div>
      <Panel>
        <PanelHeading kicker="PATH" title="ファイルパス" />
        <p className="mono wrap-path">{artifact.path || "—"}</p>
        {artifact.workspace_root && <p className="muted">Workspace ルート: {artifact.workspace_root}</p>}
      </Panel>
      <Panel>
        <PanelHeading kicker="PREVIEW" title="ファイル内容プレビュー" />
        {artifact.preview || artifact.content_preview ? (
          <ExpandableText label="全文を表示" value={artifact.preview || artifact.content_preview} />
        ) : (
          <EmptyState message="本文データは lucid-memories 内部に保管されていません。Workspace / Repository の実体ファイルを参照してください。" />
        )}
      </Panel>
    </div>
  );
}
