# Patch: add recursive chain support to process_directive
# Replaces the process_directive function and adds chain injection

def inject_next_directive(directive: dict, success: bool):
    """
    If directive has a 'next_directive' field and the current task succeeded,
    auto-inject the next task into the file queue.
    This enables recursive self-propagating build chains.
    """
    next_d = directive.get("next_directive")
    if not next_d or not success:
        return
    if isinstance(next_d, dict):
        next_d.setdefault("from", "chain_auto_inject")
        next_d.setdefault("handler", "generate_file")
        next_d.setdefault("complexity", "medium")
        idx = len(list(DIRECTIVE_DIR.glob("*.json"))) + 1
        task = next_d.get("task", "auto")
        fpath = DIRECTIVE_DIR / f"{idx:03d}_{task}_chain.json"
        fpath.write_text(json.dumps(next_d, indent=2))
        log.info(f"Chain: injected next directive -> {task}")
        mesh_event("chain_directive_injected", {"task": task, "parent": directive.get("task")})