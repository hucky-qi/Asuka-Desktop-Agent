"""Build/update the local semantic index from the command line."""

import argparse

import semantic_search


def main():
    parser = argparse.ArgumentParser(description="建立或更新本地文件语义索引")
    parser.add_argument("--scope", choices=["workspace", "desktop", "documents", "downloads", "all"], default="all")
    parser.add_argument("--max-files", type=int, default=None, help="可选扫描上限；默认扫描全部允许文件（安全上限见 settings.py）")
    args = parser.parse_args()
    print("开始建立索引；默认连接本机 Ollama，索引保存于当前项目。")
    try:
        result = semantic_search.index_files(args.scope, args.max_files, progress=print)
    except Exception as exc:
        parser.exit(1, f"索引失败：{exc}\n请确认 Ollama 已启动，并先运行 ollama pull qwen3-embedding:0.6b\n")
    print("完成：", result)


if __name__ == "__main__":
    main()
