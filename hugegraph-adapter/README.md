# HugeGraph Adapter for PowerMem

PowerMem × HugeGraph 1.7.0 适配层 — 将 PowerMem 记忆能力对接到 HugeGraph 图数据库。

## 架构

```
用户请求 → memory_backend.py (Flask :8765)
  ├── ADD 管线 (7步): V3提取 → LLM实体抽取 → 冲突检测 → 存储(HugeGraph+FAISS+BM25)
  ├── SEARCH 管线 (4步): 意图分类 → 查询改写 → 3通道融合(FAISS+BM25+Graph) → LLM回答
  └── 38+ REST API 端点
```

## 目录结构

```
hugegraph-adapter/
├── src/hugegraph_llm/
│   ├── config/memory_config.py          # 统一配置
│   ├── engines/memory/                  # 17个记忆引擎模块
│   │   ├── additive_extraction.py       # V3 ADD-only 提取
│   │   ├── graph_store.py               # HugeGraph 多跳图遍历
│   │   ├── hybrid_scoring.py            # 加性融合评分
│   │   ├── memory_history.py            # 记忆版本历史
│   │   ├── llm_query_rewrite.py         # LLM 查询改写
│   │   ├── sub_store_routing.py         # 分片路由
│   │   ├── user_profile.py              # 用户画像
│   │   ├── memory_compressor.py         # 记忆压缩
│   │   ├── agent_collaboration.py       # 多Agent协作
│   │   ├── faiss_deletable.py           # FAISS可删除索引
│   │   └── ...
│   ├── indices/fulltext/bm25_fulltext.py # BM25全文检索
│   └── poc/memory_backend.py            # 主服务 (4126行, Flask)
├── demo/
│   ├── powermem_demo.html               # 全能力交互式演示
│   ├── locomo_benchmark.html            # LOCOMO基准评测演示
│   └── locomo_server.py                 # LOCOMO数据服务器
├── tests/unit/                          # 单元测试
└── POWERMEM_HUGEGRAPH_E2E_REPORT_20260714.md  # E2E验证报告
```

## 快速启动

```bash
cd hugegraph-adapter
PYTHONPATH=src HF_HUB_OFFLINE=1 \
python src/hugegraph_llm/poc/memory_backend.py --port 8765

# LOCOMO 数据服务器
python demo/locomo_server.py 8766
```

## 验证结果

- ADD: 12实体+11关系 → HugeGraph(12 nodes+1 edge) + FAISS + BM25, 40.3s
- SEARCH: 3通道融合 → LLM回答, score=0.3957, 8.4s
- HugeGraph 1.7.0 真实存储: 47 vertices + 28 edges
- LOCOMO: 10 sessions / 5882 turns / 1986 QA pairs
