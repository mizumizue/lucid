import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api";
import {
  EmptyState,
  GlossaryIcon,
  GuideIcon,
  MetricCard,
  Panel,
  PanelHeading,
  QuickGlossaryModal,
  ResourceState,
  ViewIntro,
} from "../components";
import { formatDate, formatNumber, shortId } from "../format";
import { usePageQuery, useRefresh, useResource } from "../hooks";
import type {
  RecallGraphEdge,
  RecallGraphNode,
  RecallGraphResponse,
} from "../types";

const GRAPH_WIDTH = 1180;

export function RecallGraphPage() {
  const { searchParams, setValue } = usePageQuery();
  const { revision, refresh } = useRefresh();
  const searchInputRef = useRef<HTMLInputElement>(null);

  const conversation = searchParams.get("conversation") || "";
  const source = searchParams.get("source") || "all";
  const since = searchParams.get("since") || "";
  const until = searchParams.get("until") || "";
  const [keyword, setKeyword] = useState("");
  const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);

  const graph = useResource(
    (signal) =>
      api.recallGraph(
        {
          conversation_id: conversation || undefined,
          since: toIso(since),
          until: toIso(until, true),
          limit: 200,
        },
        signal,
      ),
    [conversation, since, until, revision],
  );

  const handleReset = () => {
    setKeyword("");
    setValue("conversation", "");
    setValue("source", "all");
    setValue("since", "");
    setValue("until", "");
    toast.info("検索・絞り込みフィルターを初期状態にリセットしました");
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        if (e.key === "Escape") {
          (e.target as HTMLElement).blur();
        }
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        refresh();
        toast.info("最新データに更新中...");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [refresh]);

  return (
    <div className="view">
      <ViewIntro
        description="ユーザーとエージェントの会話（Session）から発生した想起イベント（Retrieval）と、参照された記憶（Memory）およびナレッジマップ（Map）の因果関係を有向グラフで可視化します。"
        kicker="RETRIEVAL TRACE"
        title="会話想起グラフ (Recall Graph)"
      >
        <div className="graph-controls">
          <label>
            キーワード
            <input
              aria-label="キーワード検索"
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="検索 (/ キーでフォーカス)..."
              ref={searchInputRef}
              type="search"
              value={keyword}
            />
          </label>
          <label>
            セッション
            <select
              aria-label="会話セッション"
              onChange={(event) => setValue("conversation", event.target.value)}
              value={conversation}
            >
              <option value="">すべての会話</option>
              {conversationOptions(graph.data).map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label} ({option.count})
                </option>
              ))}
            </select>
          </label>
          <label>
            情報源
            <select
              aria-label="情報源"
              onChange={(event) => setValue("source", event.target.value)}
              value={source}
            >
              <option value="all">すべて</option>
              <option value="sqlite">記憶（SQLite）</option>
              <option value="map">Map</option>
            </select>
          </label>
          <label>
            開始日時
            <input
              aria-label="開始日時"
              onChange={(event) => setValue("since", event.target.value)}
              type="datetime-local"
              value={since}
            />
          </label>
          <label>
            終了日時
            <input
              aria-label="終了日時"
              onChange={(event) => setValue("until", event.target.value)}
              type="datetime-local"
              value={until}
            />
          </label>
          <button
            className="secondary-button reset-btn"
            onClick={handleReset}
            title="すべての検索条件・絞り込みを初期状態に戻す"
            type="button"
          >
            条件リセット
          </button>
          <button
            className="secondary-button glossary-trigger-btn"
            onClick={() => setIsGlossaryOpen(true)}
            title="主要用語の早見表を表示 (? キー)"
            type="button"
          >
            <GlossaryIcon />
            <span>用語早見表</span>
          </button>
          <Link
            className="secondary-button guide-trigger-btn"
            title="用語・機能ガイド画面を開く (/guide)"
            to="/guide"
          >
            <GuideIcon />
            <span>ガイド</span>
          </Link>
        </div>
      </ViewIntro>
      <ResourceState resource={graph}>
        {(data) => (
          <RecallGraph
            data={data}
            keyword={keyword}
            onReset={handleReset}
            source={source}
          />
        )}
      </ResourceState>
      <QuickGlossaryModal
        isOpen={isGlossaryOpen}
        onClose={() => setIsGlossaryOpen(false)}
      />
    </div>
  );
}

function RecallGraph({
  data,
  source,
  keyword,
  onReset,
}: {
  data: RecallGraphResponse;
  source: string;
  keyword: string;
  onReset: () => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const visible = useMemo(
    () => visibleGraph(data, source, keyword),
    [data, source, keyword],
  );
  const selected = visible.nodes.find((node) => node.id === selectedId) || null;

  const conversationCount = data.nodes.filter(
    (n) => n.type === "conversation",
  ).length;
  const memoryCount = data.nodes.filter((n) => n.type === "memory").length;
  const mapCount = data.nodes.filter((n) => n.type === "map").length;

  return (
    <>
      <div className="metric-grid recall-metric-grid">
        <MetricCard
          label="想起イベント"
          note={`全 ${formatNumber(data.meta.log_count)} 件`}
          value={formatNumber(data.meta.log_count)}
        />
        <MetricCard
          label="セッション数"
          note="会話ノード"
          tone="mint"
          value={conversationCount}
        />
        <MetricCard
          label="参照記憶 (SQLite)"
          note="想起された記憶"
          tone="coral"
          value={memoryCount}
        />
        <MetricCard
          label="Map 知識"
          note="オントロジー / 関係性"
          tone="yellow"
          value={mapCount}
        />
      </div>

      <div className="recall-stats-bar">
        <span>
          表示中: {visible.nodes.length} ノード · {visible.edges.length} エッジ
          {keyword ? ` (キーワード: "${keyword}")` : ""}
        </span>
        {keyword || source !== "all" ? (
          <button className="text-link-btn" onClick={onReset} type="button">
            条件をクリア
          </button>
        ) : null}
      </div>

      <div className="recall-graph-layout">
        <Panel className="recall-graph-panel">
          <PanelHeading
            action={
              <span className="panel-note">
                {formatDate(data.meta.generated_at, true)}
              </span>
            }
            kicker="RELATIONSHIP MAP"
            title="Conversation → Retrieval → Memory / Map"
          />
          {visible.nodes.length ? (
            <RecallSvg
              edges={visible.edges}
              nodes={visible.nodes}
              onSelect={setSelectedId}
              selectedId={selectedId}
            />
          ) : (
            <EmptyState message="条件に一致する想起ログはありません。キーワードまたはフィルターを変更してください。" />
          )}
          <div className="recall-legend">
            <span className="conversation">Conversation</span>
            <span className="retrieval">Retrieval</span>
            <span className="memory">Memory</span>
            <span className="map">Map</span>
          </div>
        </Panel>
        <Panel className="recall-detail-panel">
          <PanelHeading kicker="SELECTED NODE" title="ノード詳細" />
          {selected ? (
            <NodeDetails
              allNodes={data.nodes}
              edges={data.edges}
              node={selected}
              onSelectNode={setSelectedId}
            />
          ) : (
            <EmptyState message="グラフ上のノードをクリックすると、接続関係のハイライトと詳細情報が表示されます。" />
          )}
        </Panel>
      </div>
    </>
  );
}

function RecallSvg({
  edges,
  nodes,
  onSelect,
  selectedId,
}: {
  edges: RecallGraphEdge[];
  nodes: RecallGraphNode[];
  onSelect: (id: string | null) => void;
  selectedId: string | null;
}) {
  const groups = [
    ["conversation", nodes.filter((node) => node.type === "conversation")],
    ["retrieval", nodes.filter((node) => node.type === "retrieval")],
    [
      "item",
      nodes.filter((node) => node.type === "memory" || node.type === "map"),
    ],
  ] as const;
  const rowCount = Math.max(...groups.map(([, group]) => group.length), 1);
  const height = Math.max(520, rowCount * 68 + 48);
  const positions = new Map<string, { x: number; y: number }>();

  groups.forEach(([, group], groupIndex) => {
    const x = [130, 500, 910][groupIndex];
    group.forEach((node, index) => {
      positions.set(node.id, {
        x,
        y: 36 + index * ((height - 72) / Math.max(group.length - 1, 1)),
      });
    });
  });

  const connectedEdgeIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const set = new Set<string>();
    edges.forEach((edge) => {
      if (edge.source === selectedId || edge.target === selectedId) {
        set.add(edge.id);
      }
    });
    return set;
  }, [edges, selectedId]);

  const connectedNodeIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const set = new Set<string>([selectedId]);
    edges.forEach((edge) => {
      if (edge.source === selectedId) set.add(edge.target);
      if (edge.target === selectedId) set.add(edge.source);
    });
    return set;
  }, [edges, selectedId]);

  return (
    <div
      className="recall-graph-canvas"
      onClick={(e) => {
        if (
          e.target === e.currentTarget ||
          (e.target as HTMLElement).tagName === "svg"
        ) {
          onSelect(null);
        }
      }}
    >
      <svg
        aria-label="会話と想起された記憶の関係グラフ"
        height={height}
        role="img"
        viewBox={`0 0 ${GRAPH_WIDTH} ${height}`}
        width="100%"
      >
        {edges.map((edge) => {
          const from = positions.get(edge.source);
          const to = positions.get(edge.target);
          if (!from || !to) return null;
          const isHighlighted = connectedEdgeIds.has(edge.id);
          const isDimmed = selectedId !== null && !isHighlighted;
          const baseWidth =
            edge.count && edge.count > 1
              ? Math.min(5, 1.2 + Math.log2(edge.count) * 1.5)
              : 1.2;
          const strokeWidth = isHighlighted
            ? Math.max(2.5, baseWidth + 1)
            : baseWidth;

          return (
            <line
              className={`recall-edge ${
                edge.relation === "recalled" ? "recalled" : "map-relation"
              }${isHighlighted ? " highlighted" : ""}${
                isDimmed ? " dimmed" : ""
              }`}
              key={edge.id}
              style={{ strokeWidth }}
              x1={from.x}
              x2={to.x}
              y1={from.y}
              y2={to.y}
            >
              <title>
                {edge.relation} ({edge.count || 1})
              </title>
            </line>
          );
        })}
        {nodes.map((node) => {
          const point = positions.get(node.id);
          if (!point) return null;
          const isRetrieval = node.type === "retrieval";
          const isSelected = selectedId === node.id;
          const isHighlighted = connectedNodeIds.has(node.id);
          const isDimmed = selectedId !== null && !isHighlighted;
          const radius = Math.max(
            7,
            Math.min(24, 8 + Number(node.temperature || 0) * 18),
          );

          const nodeClass = `recall-node ${node.type}${
            isSelected ? " selected" : ""
          }${isHighlighted ? " highlighted" : ""}${isDimmed ? " dimmed" : ""}`;

          return (
            <g
              className="recall-node-group"
              key={node.id}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(isSelected ? null : node.id);
              }}
            >
              {isRetrieval ? (
                <rect
                  className={nodeClass}
                  height="16"
                  rx="4"
                  width="16"
                  x={point.x - 8}
                  y={point.y - 8}
                />
              ) : (
                <circle
                  className={nodeClass}
                  cx={point.x}
                  cy={point.y}
                  r={radius}
                />
              )}
              <text
                className={`recall-node-label${
                  isHighlighted ? " highlighted" : ""
                }${isDimmed ? " dimmed" : ""}`}
                x={point.x + 16}
                y={point.y + 4}
              >
                {shortLabel(node.label || node.id)}
                {node.retrieval_count && node.retrieval_count > 1 ? (
                  <tspan className="recall-badge" dx="6">
                    {`×${node.retrieval_count}`}
                  </tspan>
                ) : null}
              </text>
              <title>
                {node.label || node.id}
                {node.retrieval_count && node.retrieval_count > 1
                  ? ` (${node.retrieval_count}回想起)`
                  : ""}
              </title>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function NodeDetails({
  edges,
  node,
  allNodes,
  onSelectNode,
}: {
  edges: RecallGraphEdge[];
  node: RecallGraphNode;
  allNodes: RecallGraphNode[];
  onSelectNode: (id: string) => void;
}) {
  const linked = edges.filter(
    (edge) => edge.source === node.id || edge.target === node.id,
  );

  const nodeMap = useMemo(
    () => new Map(allNodes.map((n) => [n.id, n])),
    [allNodes],
  );

  const copyToClipboard = (text: string, label: string) => {
    const fallbackCopy = () => {
      try {
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.style.position = "fixed";
        textArea.style.left = "-9999px";
        textArea.style.top = "-9999px";
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
        toast.success("クリップボードにコピーしました", {
          description: label,
        });
      } catch (err) {
        toast.error("コピーに失敗しました", {
          description: String(err),
        });
      }
    };

    if (navigator?.clipboard?.writeText) {
      navigator.clipboard
        .writeText(text)
        .then(() => {
          toast.success("クリップボードにコピーしました", {
            description: label,
          });
        })
        .catch(() => {
          fallbackCopy();
        });
    } else {
      fallbackCopy();
    }
  };

  return (
    <div className="recall-node-details">
      <div className="recall-detail-header">
        <strong>{node.label || node.id}</strong>
        <div className="detail-action-buttons">
          <button
            className="mini-copy-btn"
            onClick={() =>
              copyToClipboard(
                JSON.stringify(node, null, 2),
                "ノードの完全なJSONデータ",
              )
            }
            title="ノードJSONをコピー"
            type="button"
          >
            JSONコピー
          </button>
          {node.prompt ? (
            <button
              className="mini-copy-btn"
              onClick={() => copyToClipboard(node.prompt || "", "プロンプト全文")}
              title="プロンプトをコピー"
              type="button"
            >
              プロンプトコピー
            </button>
          ) : null}
          {node.body_preview ? (
            <button
              className="mini-copy-btn"
              onClick={() =>
                copyToClipboard(node.body_preview || "", "記憶の本文プレビュー")
              }
              title="本文プレビューをコピー"
              type="button"
            >
              本文コピー
            </button>
          ) : null}
        </div>
      </div>

      <div className="status-summary">
        <div className="status-summary-row">
          <span>ノード種別</span>
          <strong>{node.type}</strong>
        </div>
        <div className="status-summary-row">
          <span>ノードID</span>
          <div className="inline-copy-target">
            <code className="mono">{shortId(node.id)}</code>
            <button
              className="icon-copy-btn"
              onClick={() => copyToClipboard(node.id, `ノードID: ${node.id}`)}
              title="IDをコピー"
              type="button"
            >
              📋
            </button>
          </div>
        </div>
        <div className="status-summary-row">
          <span>想起回数</span>
          <strong>{node.retrieval_count ?? "—"}</strong>
        </div>
        <div className="status-summary-row">
          <span>最終想起日時</span>
          <strong>{formatDate(node.last_recalled_at, true)}</strong>
        </div>
      </div>

      {node.prompt ? (
        <div className="detail-section">
          <span className="panel-kicker">PROMPT</span>
          <pre className="recall-node-text">{node.prompt}</pre>
        </div>
      ) : null}

      {node.body_preview ? (
        <div className="detail-section">
          <span className="panel-kicker">BODY PREVIEW</span>
          <pre className="recall-node-text">{node.body_preview}</pre>
        </div>
      ) : null}

      <div className="recall-links">
        <span className="panel-kicker">CONNECTED LINKS ({linked.length})</span>
        {linked.length ? (
          <div className="linked-list">
            {linked.map((edge) => {
              const otherId =
                edge.source === node.id ? edge.target : edge.source;
              const otherNode = nodeMap.get(otherId);
              const label = otherNode ? otherNode.label || otherId : otherId;
              const direction = edge.source === node.id ? "→" : "←";
              return (
                <button
                  className="linked-node-item"
                  key={edge.id}
                  onClick={() => onSelectNode(otherId)}
                  title={`ノード "${label}" を選択`}
                  type="button"
                >
                  <span className="link-rel">
                    {direction} {edge.relation}
                  </span>
                  <span className="link-target">{shortLabel(label, 30)}</span>
                  {edge.count && edge.count > 1 ? (
                    <span className="link-count">×{edge.count}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="empty-subtext">接続リンクはありません</div>
        )}
      </div>
    </div>
  );
}

function visibleGraph(
  data: RecallGraphResponse,
  source: string,
  keyword: string,
) {
  const normKeyword = keyword.trim().toLowerCase();
  const nodes = data.nodes.filter((node) => {
    if (
      source !== "all" &&
      node.type !== "conversation" &&
      node.type !== "retrieval" &&
      node.database !== source
    ) {
      return false;
    }
    if (normKeyword) {
      const matchLabel = (node.label || "").toLowerCase().includes(normKeyword);
      const matchId = (node.id || "").toLowerCase().includes(normKeyword);
      const matchPrompt = (node.prompt || "")
        .toLowerCase()
        .includes(normKeyword);
      const matchBody = (node.body_preview || "")
        .toLowerCase()
        .includes(normKeyword);
      if (!matchLabel && !matchId && !matchPrompt && !matchBody) return false;
    }
    return true;
  });
  const ids = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: data.edges.filter(
      (edge) => ids.has(edge.source) && ids.has(edge.target),
    ),
  };
}

function conversationOptions(data: RecallGraphResponse | null) {
  return (data?.nodes || [])
    .filter((node) => node.type === "conversation" && node.conversation_id)
    .map((node) => {
      const id = node.conversation_id as string;
      const label =
        node.label && node.label !== id
          ? node.label
          : `セッション (${shortId(id)})`;
      return {
        id,
        label,
        count: node.retrieval_count || 0,
      };
    });
}

function shortLabel(value: string, length = 46) {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function toIso(value: string, endOfDay = false) {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  if (endOfDay && value.length === 10) date.setHours(23, 59, 59, 999);
  return date.toISOString();
}
