//! Destructive-git discard guard: detects git commands that discard uncommitted
//! working-tree changes, resolves which repository each discard targets (cd
//! chains, `git -C`, inline env assignments), and formats the dirty-tree refusal.

/// Bypass env var for the destructive-git dirty-tree guard.
pub(crate) const BASH_DESTRUCTIVE_GIT_BYPASS_ENV: &str = "PI_BASH_ALLOW_DESTRUCTIVE_GIT";

/// How many dirty paths the refusal lists before eliding the rest.
pub(crate) const MAX_DIRTY_PATHS_LISTED: usize = 10;

/// JavaScript `\s` character class.
const S: &str = r"[\t\n\x0B\f\r \u{00A0}\u{1680}\u{2000}-\u{200A}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}\u{FEFF}]";

/// JS `str.split(/\s+/)` (leading/trailing empty tokens removed).
fn split_ws(text: &str) -> Vec<&str> {
    let pattern = format!("^{S}+|{S}+");
    let re = fancy_regex::Regex::new(&pattern).expect("split_ws regex");
    let mut parts = Vec::new();
    let mut rest = text;
    while let Some(m) = re.find(rest).ok().flatten() {
        if m.start() > 0 {
            parts.push(&rest[..m.start()]);
        }
        rest = &rest[m.end()..];
    }
    if !rest.is_empty() {
        parts.push(rest);
    }
    parts
}

/// git global options between `git` and the subcommand.
const GIT_GLOBAL_OPTIONS: &str = r#"(?:-{1,2}[^\s;&|]+(?:\s+(?:"[^"]*"|'[^']*'|[^\s;&|]+))?\s+)*"#;

fn discard_checkout_pattern() -> fancy_regex::Regex {
    fancy_regex::Regex::new(&format!(
        r"\bgit\s+{GIT_GLOBAL_OPTIONS}checkout\s+(?:(?:(?:-[fm]|--ours|--theirs|--conflict=\S+)\s+)*(?:--\s+)?(?:\.\/?|:\/)|[^\s;&|()]+\s+(?:--\s+)?(?:\.\/?|:\/)|(?:-f|--force)\s+[^\s;&|()]+)(?=\s|$|[;&|)])"
    ))
    .expect("checkout discard regex")
}

fn discard_restore_pattern() -> fancy_regex::Regex {
    fancy_regex::Regex::new(&format!(
        r"\bgit\s+{GIT_GLOBAL_OPTIONS}restore\s+(?:(?:--source|--worktree)(?:=\S+)?\s+|-s(?:\s+\S+|[^\s]+)\s+|-W\s+|--\s+)?(?:\.\/?|:\/)(?=\s|$|[;&|)])"
    ))
    .expect("restore discard regex")
}

fn discard_reset_pattern() -> fancy_regex::Regex {
    fancy_regex::Regex::new(&format!(
        r"\bgit\s+{GIT_GLOBAL_OPTIONS}reset\s+(?:(?:-[^\s;&|]+)\s+)*--hard\b"
    ))
    .expect("reset discard regex")
}

fn discard_clean_pattern() -> fancy_regex::Regex {
    fancy_regex::Regex::new(&format!(r"\bgit\s+{GIT_GLOBAL_OPTIONS}clean\s+([^;&|]*)"))
        .expect("clean discard regex")
}

fn is_forced_clean_segment(args: &str) -> bool {
    let tokens: Vec<&str> = split_ws(args);
    let option_end = tokens.iter().position(|t| *t == "--");
    let option_tokens: &[&str] = match option_end {
        Some(end) => &tokens[..end],
        None => &tokens,
    };
    let forces: Vec<&&str> = option_tokens
        .iter()
        .filter(|arg| {
            if arg.starts_with("--") {
                arg.starts_with("--force")
            } else {
                arg.starts_with('-') && arg.contains('f')
            }
        })
        .collect();
    if forces.is_empty() {
        return false;
    }
    !option_tokens.iter().any(|arg| {
        *arg == "--dry-run" || (arg.starts_with('-') && !arg.starts_with("--") && arg.contains('n'))
    })
}

/// Replace characters inside single- or double-quoted spans with spaces so the discard matcher
/// cannot match quoted data; character positions stay identical. Command substitution (`$(...)`,
/// backticks) is left live because it executes; unquoted `#` at a word boundary starts a comment.
fn mask_quoted_spans(command: &str) -> Vec<char> {
    let mut chars: Vec<char> = command.chars().collect();
    let mut quote: Option<char> = None;
    let len = chars.len();
    let mut i = 0usize;
    while i < len {
        let ch = chars[i];
        if quote.is_none() {
            let prev = if i > 0 { Some(chars[i - 1]) } else { None };
            if ch == '#'
                && (i == 0
                    || prev.is_none_or(|p| {
                        p.is_whitespace() || matches!(p, ';' | '&' | '|' | '(' | ')' | '{' | '}')
                    }))
            {
                let mut j = i;
                while j < len && chars[j] != '\n' {
                    chars[j] = ' ';
                    j += 1;
                }
                i = j;
                continue;
            }
            if ch == '"' || ch == '\'' {
                quote = Some(ch);
            }
        } else if quote == Some('\'') {
            if ch == '\'' {
                quote = None;
            } else {
                chars[i] = ' ';
            }
        } else if ch == '"' {
            quote = None;
        } else if ch == '\\' && i + 1 < len {
            chars[i] = ' ';
            chars[i + 1] = ' ';
            i += 1;
        } else if ch == '$' && i + 1 < len && chars[i + 1] == '(' {
            let mut depth = 0i32;
            let mut j = i;
            while j < len {
                if chars[j] == '(' {
                    depth += 1;
                } else if chars[j] == ')' {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
                j += 1;
            }
            i = j.saturating_sub(1);
        } else if ch == '`' {
            let mut j = i + 1;
            while j < len && chars[j] != '`' {
                j += 1;
            }
            i = j.saturating_sub(1);
        } else {
            chars[i] = ' ';
        }
        i += 1;
    }
    chars
}

/// Find every destructive git discard command in `command`, returning the
/// byte index where each `git` token starts (empty when none match).
pub fn find_destructive_git_discard_commands(command: &str) -> Vec<usize> {
    let masked: String = mask_quoted_spans(command).into_iter().collect();
    let mut indices: Vec<usize> = Vec::new();
    for pattern in [
        discard_checkout_pattern(),
        discard_restore_pattern(),
        discard_reset_pattern(),
    ] {
        for m in pattern.find_iter(&masked).flatten() {
            indices.push(m.start());
        }
    }
    for caps in discard_clean_pattern().captures_iter(&masked).flatten() {
        if let Some(args) = caps.get(1) {
            if is_forced_clean_segment(args.as_str()) {
                indices.push(caps.get(0).map(|m| m.start()).unwrap_or_default());
            }
        }
    }
    indices.sort_unstable();
    indices
}

/// True when `command` contains a git discard command.
#[allow(dead_code)]
pub fn is_destructive_git_discard_command(command: &str) -> bool {
    !find_destructive_git_discard_commands(command).is_empty()
}

/// Where a discard command's probe must run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct DiscardProbeTarget {
    /// Shell prefix relocating the probe, for example `cd sub && `.
    pub relocation_prefix: Option<String>,
    /// git status command for this discard.
    pub git_status_command: String,
}

/// Result of resolving a discard command's probe location.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum DiscardProbeResolution {
    /// No relocation is needed; probe in the tool cwd.
    NotRelocated,
    /// The target repository cannot be resolved safely; refuse.
    Unresolvable,
    /// Probe must run with the given relocation.
    Target(DiscardProbeTarget),
}

/// Shell separators treated by segment splitting.
const SEPARATORS: [&str; 5] = ["&&", "||", ";", "|", "\n"];

/// Split keeping separators (TS: `split(/(&&|\|\||;|\||\n)/)`). Runs on
/// every bash command before execution, so it must stay linear: the old
/// per-position `chars[i..]` collection made it quadratic, and a
/// 100,000-character heredoc stalled the agent for seconds before the
/// command even ran.
fn split_with_separators(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let len = chars.len();
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut i = 0usize;
    while i < len {
        let sep = match chars[i] {
            '&' if i + 1 < len && chars[i + 1] == '&' => Some("&&"),
            '|' if i + 1 < len && chars[i + 1] == '|' => Some("||"),
            '|' => Some("|"),
            ';' => Some(";"),
            '\n' => Some("\n"),
            _ => None,
        };
        if let Some(sep) = sep {
            if !current.is_empty() {
                parts.push(std::mem::take(&mut current));
            }
            parts.push(sep.to_string());
            i += sep.chars().count();
        } else {
            current.push(chars[i]);
            i += 1;
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

/// Split on separators, dropping them (TS: `split(/&&|\|\||;|\||\n/)`).
fn split_on_separators(text: &str) -> Vec<String> {
    split_with_separators(text)
        .into_iter()
        .filter(|part| !SEPARATORS.contains(&part.as_str()))
        .collect()
}

pub fn resolve_discard_probe_target(
    command: &str,
    discard_index: usize,
    user_command_start: usize,
) -> DiscardProbeResolution {
    let prefix = &command[..discard_index];
    let invocation = &command[discard_index..];
    // A discard inside the configured command prefix would be replayed by the
    // probe itself; refuse instead of executing it during probing.
    if user_command_start > 0 && discard_index < user_command_start {
        return DiscardProbeResolution::Unresolvable;
    }
    let tokens = split_ws(invocation);

    // git -C <dir> (or repository-relocating global options) on the discard invocation itself.
    let mut dash_c_dir: Option<String> = None;
    let mut subcommand_index: Option<usize> = None;
    for (index, token) in tokens.iter().enumerate() {
        if index == 0 {
            continue; // "git"
        }
        if *token == "reset" || *token == "checkout" || *token == "clean" || *token == "restore" {
            subcommand_index = Some(index);
            break;
        }
        if *token == "-C" {
            let dir = tokens.get(index + 1).copied();
            // A quoted, escaped, or substituted path cannot be replayed as a
            // single token; refuse rather than probe a truncated directory.
            let Some(dir) = dir else {
                return DiscardProbeResolution::Unresolvable;
            };
            if dir.contains(['"', '\'', '\\', '$', '`']) {
                return DiscardProbeResolution::Unresolvable;
            }
            // Repeated -C paths are relative to the preceding one.
            dash_c_dir = Some(match dash_c_dir {
                Some(existing) => format!("{existing} -C {dir}"),
                None => dir.to_string(),
            });
        } else if token.starts_with("--git-dir")
            || token.starts_with("--work-tree")
            || token.starts_with("--prefix")
        {
            return DiscardProbeResolution::Unresolvable;
        } else if *token == "-c" {
            let config = tokens.get(index + 1).copied();
            // core.worktree/core.bare relocate the repository.
            if let Some(config) = config {
                if config.starts_with("core.worktree") || config.starts_with("core.bare") {
                    let relocates = config
                        .strip_prefix("core.worktree")
                        .or_else(|| config.strip_prefix("core.bare"))
                        .is_some_and(|rest| rest.is_empty() || rest.starts_with('='));
                    if relocates {
                        return DiscardProbeResolution::Unresolvable;
                    }
                }
            }
        }
        // Other flags do not relocate.
    }

    // git clean -x/-X also deletes ignored files, so its probe includes them.
    let mut clean_removes_ignored = false;
    if let Some(sub_index) = subcommand_index {
        if tokens[sub_index] == "clean" {
            for token in &tokens[sub_index + 1..] {
                if *token == "--" {
                    break; // everything after -- is a pathspec
                }
                if token.starts_with("--") {
                    continue;
                }
                if token.starts_with('-') && token[1..].contains(['x', 'X']) {
                    clean_removes_ignored = true;
                    break;
                }
            }
        }
    }

    // Inline env assignments directly before the git invocation relocate the
    // target repository; replay them in the probe, or refuse when they cannot.
    let last_segment = split_on_separators(prefix).pop().unwrap_or_default();
    let leading_tokens: Vec<&str> = split_ws(last_segment.trim());
    let assignment_re = fancy_regex::Regex::new(r#"^[A-Za-z_][A-Za-z0-9_]*=[^\s$`;&|()<>"!]+$"#)
        .expect("assignment regex");
    for token in &leading_tokens {
        if assignment_re.is_match(token).unwrap_or(false) {
            continue; // replayable assignment
        }
        // Wrappers that cannot change directory or select another repository.
        if *token == "sudo"
            || *token == "env"
            || *token == "command"
            || *token == "builtin"
            || token.ends_with('/')
        {
            continue;
        }
        return DiscardProbeResolution::Unresolvable;
    }
    let assignments: Vec<&str> = leading_tokens
        .iter()
        .copied()
        .filter(|token| token.contains('='))
        .collect();
    let env_prefix = if assignments.is_empty() {
        String::new()
    } else {
        format!("{} ", assignments.join(" "))
    };

    // cd relocations earlier in the command.
    let cd_re = fancy_regex::Regex::new(r"\b(cd|pushd)\b").expect("cd regex");
    let has_cd = cd_re.is_match(prefix).unwrap_or(false) || prefix.contains('(');
    let mut persistent_cd_args: Vec<String> = Vec::new();
    let mut grouped_cd_args: Vec<String> = Vec::new();
    let mut saw_cd = false;
    let mut paren_depth: i64 = 0;
    let mut cd_pending_separator = false;
    if has_cd {
        let mut offset = 0usize;
        for part in split_with_separators(prefix) {
            let start = offset;
            offset += part.len();
            if start < user_command_start {
                continue; // command-prefix region: replayed as-is
            }
            let is_separator = SEPARATORS.contains(&part.as_str());
            if is_separator {
                if cd_pending_separator && (part == ";" || part == "\n") {
                    // The discard's directory depends on the cd succeeding.
                    return DiscardProbeResolution::Unresolvable;
                }
                if part == "||" || part == "|" {
                    if saw_cd {
                        return DiscardProbeResolution::Unresolvable;
                    }
                    continue;
                }
                cd_pending_separator = false;
                continue;
            }
            let trimmed = part.trim();
            let opens = part.matches('(').count() as i64;
            let closes = part.matches(')').count() as i64;
            let inside_group = paren_depth > 0 || opens > 0;
            paren_depth = 0.max(paren_depth + opens - closes);
            if inside_group {
                let stripped: String = trimmed
                    .trim_start_matches(|c: char| c == '(' || c.is_whitespace())
                    .trim_end_matches(|c: char| c == ')' || c.is_whitespace())
                    .to_string();
                if let Some(arg) = stripped.strip_prefix("cd") {
                    let arg = arg.trim();
                    if !arg.is_empty()
                        && arg.contains(['$', '`', ';', '&', '|', '(', ')', '<', '>', '#', '"'])
                    {
                        return DiscardProbeResolution::Unresolvable;
                    }
                    saw_cd = true;
                    cd_pending_separator = true;
                    grouped_cd_args.push(arg.to_string());
                } else if cd_re.is_match(trimmed).unwrap_or(false) {
                    return DiscardProbeResolution::Unresolvable;
                }
                if paren_depth == 0 {
                    grouped_cd_args.clear();
                }
                continue;
            }
            if trimmed == "pushd" || trimmed.starts_with("pushd ") {
                return DiscardProbeResolution::Unresolvable;
            }
            let Some(arg) = trimmed.strip_prefix("cd") else {
                cd_pending_separator = false;
                continue; // not a cd: cannot change cwd
            };
            let arg = arg.trim();
            // An arg we cannot replay safely leaves the target repository
            // unknown; refuse rather than probe blindly.
            let balanced = arg.matches('"').count() % 2 == 0 && arg.matches('\'').count() % 2 == 0;
            if !balanced
                || (!arg.is_empty()
                    && arg.contains(['$', '`', ';', '&', '|', '(', ')', '<', '>', '#']))
            {
                return DiscardProbeResolution::Unresolvable;
            }
            saw_cd = true;
            cd_pending_separator = true;
            persistent_cd_args.push(arg.to_string());
        }
    }
    let cd_args = if paren_depth > 0 {
        let mut args = persistent_cd_args.clone();
        args.extend(grouped_cd_args);
        args
    } else {
        persistent_cd_args
    };

    if cd_args.is_empty() && dash_c_dir.is_none() && !clean_removes_ignored && env_prefix.is_empty()
    {
        return DiscardProbeResolution::NotRelocated;
    }
    let ignored = if clean_removes_ignored {
        " --ignored=matching"
    } else {
        ""
    };
    let cd_prefix = if cd_args.is_empty() {
        String::new()
    } else {
        let joins = cd_args
            .iter()
            .map(|arg| {
                if arg.is_empty() {
                    "cd".to_string()
                } else {
                    format!("cd {arg}")
                }
            })
            .collect::<Vec<_>>()
            .join(" && ");
        format!("{joins} && ")
    };
    let relocation = format!("{cd_prefix}{env_prefix}");
    DiscardProbeResolution::Target(DiscardProbeTarget {
        relocation_prefix: if relocation.is_empty() {
            None
        } else {
            Some(relocation)
        },
        git_status_command: format!(
            "{}status --porcelain --untracked-files=all{ignored}",
            match dash_c_dir.as_deref() {
                Some(dir) => format!("git -C {dir} "),
                None => "git ".to_string(),
            }
        ),
    })
}

pub(crate) fn is_truthy_env_value(value: Option<&String>) -> bool {
    match value {
        Some(v) => !v.is_empty() && v != "0",
        None => false,
    }
}

pub(crate) fn format_dirty_tree_refusal(
    dirty_paths: &[String],
    includes_ignored_files: bool,
) -> String {
    let listed_count = dirty_paths.len().min(MAX_DIRTY_PATHS_LISTED);
    let listed = &dirty_paths[..listed_count];
    let elided = dirty_paths.len() - listed_count;
    let noun = if includes_ignored_files {
        "uncommitted or ignored file(s)"
    } else {
        "uncommitted change(s)"
    };
    let mut lines = vec![format!(
        "Refusing to run this destructive git command: the working tree has {} {noun}.",
        dirty_paths.len()
    )];
    lines.extend(listed.iter().map(|line| format!("  {line}")));
    if elided > 0 {
        lines.push(format!("  ... and {elided} more"));
    }
    lines.push(String::new());
    lines.push("Commit, stash, or stage your work first.".to_string());
    lines.push(format!(
        "To discard these changes intentionally, retry with allowDestructiveGit: true, or set {BASH_DESTRUCTIVE_GIT_BYPASS_ENV}=1."
    ));
    lines.join("\n")
}

/// Env var opt-in: seconds for the auto-timeout applied to matching unbounded
/// scans; unset or non-positive means off (the scan still runs, only warned).
pub(crate) const SCAN_TIMEOUT_ENV: &str = "PRIME_AGENT_SCAN_TIMEOUT";

/// A home-scale scan root: `~`-rooted paths and `$HOME` expansions walk the
/// user's whole home tree, so a recursive scan there costs minutes, not
/// milliseconds. Exact-name tests keep other env vars (`$HOMEDIR`,
/// `${HOMEBREW_PREFIX}`) out.
fn is_home_scale_root(token: &str) -> bool {
    token.starts_with('~')
        || token == "$HOME"
        || token.starts_with("$HOME/")
        || token == "${HOME}"
        || token.starts_with("${HOME}/")
}

/// True when a depth bound appears with a valid numeric argument: rg's
/// `--max-depth[=]N`/`-d N`/`-dN`, find's `-maxdepth N`. A bare token (for
/// example the filename `maxdepth.txt`) never counts.
fn is_depth_bound_token(token: &str, next: Option<&str>) -> bool {
    let is_number = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
    ((token == "--max-depth" || token == "-maxdepth" || token == "-d")
        && next.is_some_and(is_number))
        || token.strip_prefix("--max-depth=").is_some_and(is_number)
        || token.strip_prefix("-maxdepth=").is_some_and(is_number)
        || token.strip_prefix("-d").is_some_and(is_number)
}

/// Mask single-quoted spans, double-quoted literal data, unquoted comments,
/// and heredoc bodies. Double quotes still expand `$HOME`/`${HOME}` and run
/// command substitution, so inside a double-quoted span only those stay
/// visible, plus all-digit spans (a quoted `--max-depth "3"` argument is a
/// bound, not data). Heredoc bodies are input text, never executed, so the
/// whole body through the delimiter line is masked.
fn mask_scan_literals(command: &str) -> String {
    let mut chars: Vec<char> = command.chars().collect();
    let len = chars.len();
    let mut i = 0usize;
    while i < len {
        let ch = chars[i];
        if ch == '\'' {
            // An all-digit quoted span is an argument (`-maxdepth '2'`), not
            // data: keep the digits visible for bound detection.
            let mut j = i + 1;
            while j < len && chars[j].is_ascii_digit() {
                j += 1;
            }
            if j > i + 1 && chars.get(j) == Some(&'\'') {
                chars[i] = ' ';
                chars[j] = ' ';
                i = j + 1;
                continue;
            }
            let mut j = i + 1;
            while j < len && chars[j] != '\'' {
                chars[j] = ' ';
                j += 1;
            }
            if j < len {
                chars[j] = ' ';
            }
            i = j + 1;
            continue;
        }
        if ch == '<'
            && chars.get(i + 1) == Some(&'<')
            && chars.get(i + 2) != Some(&'<')
            && (i == 0 || chars[i - 1].is_whitespace())
        {
            // Heredoc `<<[-]DELIM`: the body until the delimiter line is
            // input text, never executed, so mask it. The rest of the
            // heredoc-start line stays live command text.
            let mut j = i + 2;
            let mut tabbed = false;
            if chars.get(j) == Some(&'-') {
                tabbed = true;
                j += 1;
            }
            let mut word = String::new();
            while j < len && !chars[j].is_whitespace() {
                word.push(chars[j]);
                j += 1;
            }
            let delim = word.trim_matches(|c| c == '\'' || c == '"');
            if !delim.is_empty() {
                let mut body = j;
                while body < len && chars[body] != '\n' {
                    body += 1;
                }
                if body < len {
                    body += 1; // first char of the body
                    loop {
                        let mut eol = body;
                        while eol < len && chars[eol] != '\n' {
                            eol += 1;
                        }
                        let mut start = body;
                        if tabbed {
                            while start < eol && chars[start] == '\t' {
                                start += 1;
                            }
                        }
                        let terminator = chars[start..eol].iter().collect::<String>() == delim;
                        for c in &mut chars[body..eol] {
                            *c = ' ';
                        }
                        body = eol + 1;
                        if terminator || eol >= len {
                            break;
                        }
                    }
                }
            }
            i += 2;
            continue;
        }
        if ch == '"' {
            // An all-digit quoted span is an argument (`--max-depth "3"`),
            // not data: keep the digits visible for bound detection.
            let mut j = i + 1;
            while j < len && chars[j].is_ascii_digit() {
                j += 1;
            }
            if j > i + 1 && chars.get(j) == Some(&'"') {
                chars[i] = ' ';
                chars[j] = ' ';
                i = j + 1;
                continue;
            }
            chars[i] = ' ';
            i += 1;
            while i < len && chars[i] != '"' {
                if chars[i] == '\\' && i + 1 < len {
                    chars[i] = ' ';
                    chars[i + 1] = ' ';
                    i += 2;
                } else if chars[i] == '$' && i + 1 < len && chars[i + 1] == '(' {
                    let mut depth = 0i32;
                    let mut j = i;
                    while j < len {
                        if chars[j] == '(' {
                            depth += 1;
                        } else if chars[j] == ')' {
                            depth -= 1;
                            if depth == 0 {
                                break;
                            }
                        }
                        j += 1;
                    }
                    i = if j < len { j + 1 } else { len };
                } else if chars[i] == '`' {
                    i += 1;
                    while i < len && chars[i] != '`' {
                        i += 1;
                    }
                    i += 1;
                } else if chars[i] == '$' && starts_with_home_expansion(&chars, i) {
                    i += home_expansion_len(&chars, i);
                } else {
                    chars[i] = ' ';
                    i += 1;
                }
            }
            if i < len {
                chars[i] = ' '; // closing quote
            }
            i += 1;
            continue;
        }
        if ch == '#' && (i == 0 || chars[i - 1].is_whitespace()) {
            while i < len && chars[i] != '\n' {
                chars[i] = ' ';
                i += 1;
            }
            continue;
        }
        i += 1;
    }
    chars.into_iter().collect()
}

/// True when `chars[at..]` starts with the `$HOME` or `${HOME}` expansion.
/// A following variable-name character (`$HOMEDIR`) is a different variable.
fn starts_with_home_expansion(chars: &[char], at: usize) -> bool {
    let is_name_char = |c: char| c.is_ascii_alphanumeric() || c == '_';
    if chars.get(at + 1) == Some(&'{') {
        let rest: String = chars[at..chars.len().min(at + 7)].iter().collect();
        rest.starts_with("${HOME}")
    } else {
        let rest: String = chars[at..chars.len().min(at + 5)].iter().collect();
        rest == "$HOME" && chars.get(at + 5).is_none_or(|c| !is_name_char(*c))
    }
}

/// The length of the `$HOME` (5) or `${HOME}` (7) expansion at `at`.
fn home_expansion_len(chars: &[char], at: usize) -> usize {
    if chars.get(at + 1) == Some(&'{') {
        7
    } else {
        5
    }
}

/// True when `command` runs a recursive scan (grep -r, rg, find) over a
/// home-scale root with no bound: no tool timeout, no `timeout` prefix, and no
/// depth limit. Output caps (`| head`) never bound scan time — a no-match scan
/// still walks every file — so this is the incident shape: a recursive grep
/// over six home-scale directories piped to `head` held a turn for 13 minutes.
/// The bash tool call is awaited inline by the agent loop, so an unbounded
/// scan stalls the whole turn; callers warn (never block), because searches
/// are legitimate.
pub(crate) fn is_unbounded_home_scan(command: &str, tool_timeout: Option<f64>) -> bool {
    // A non-positive tool timeout is no deadline for the executor
    // (bash_local filters `t > 0.0`), so it does not bound the scan.
    if tool_timeout.is_some_and(|t| t > 0.0) {
        return false;
    }
    scan_command_text(command)
}

/// Scan-detect on the masked command plus every command-substitution body:
/// `echo "$(grep -rl needle ~/Documents)"` runs the scan in a subshell, so a
/// substitution body is a command too (and so are its own substitutions).
fn scan_command_text(command: &str) -> bool {
    let masked = mask_scan_literals(command);
    if segments_contain_unbounded_scan(&masked) {
        return true;
    }
    substitution_bodies(&masked)
        .into_iter()
        .any(|body| scan_command_text(&body))
}

/// Bodies of `$(...)` spans (paren-depth matched) and backticks in a masked
/// command: these execute, so their text is command text.
fn substitution_bodies(masked: &str) -> Vec<String> {
    let chars: Vec<char> = masked.chars().collect();
    let len = chars.len();
    let mut bodies = Vec::new();
    let mut i = 0usize;
    while i < len {
        if chars[i] == '$' && chars.get(i + 1) == Some(&'(') {
            let mut depth = 0i32;
            let mut j = i + 1;
            while j < len {
                if chars[j] == '(' {
                    depth += 1;
                } else if chars[j] == ')' {
                    depth -= 1;
                    if depth == 0 {
                        let body: String = chars[i + 2..j].iter().collect();
                        if !body.trim().is_empty() {
                            bodies.push(body);
                        }
                        i = j;
                        break;
                    }
                }
                j += 1;
            }
        } else if chars[i] == '`' {
            if let Some(j) = chars[i + 1..].iter().position(|&c| c == '`') {
                let end = i + 1 + j;
                let body: String = chars[i + 1..end].iter().collect();
                if !body.trim().is_empty() {
                    bodies.push(body);
                }
                i = end;
            }
        }
        i += 1;
    }
    bodies
}

fn segments_contain_unbounded_scan(masked: &str) -> bool {
    for segment in split_on_separators(masked) {
        let tokens = split_ws(segment.trim());
        let Some((first, rest)) = tokens.split_first() else {
            continue;
        };
        if *first == "timeout" {
            continue; // bounded by the timeout(1) prefix
        }
        let rest: &[&str] = match *first {
            "grep" | "egrep" | "fgrep" => {
                let recursive = rest.iter().any(|t| {
                    t.starts_with("--recursive")
                        || (t.starts_with('-')
                            && !t.starts_with("--")
                            && t.len() > 1
                            && t.to_ascii_lowercase().contains('r'))
                });
                if recursive {
                    rest
                } else {
                    continue;
                }
            }
            // rg and find recurse by default.
            "rg" | "find" => rest,
            _ => continue,
        };
        if !rest.iter().any(|t| is_home_scale_root(t)) {
            continue; // project-local scans are bounded by the tree size
        }
        let bounded = rest
            .iter()
            .enumerate()
            .any(|(i, t)| is_depth_bound_token(t, rest.get(i + 1).copied()));
        if !bounded {
            return true;
        }
    }
    false
}

/// The opted-in auto-timeout for matching scans from the environment, if set.
pub(crate) fn scan_timeout_from_env(
    env: &std::collections::HashMap<String, String>,
) -> Option<f64> {
    env.get(SCAN_TIMEOUT_ENV)
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|secs| {
            // `Duration::from_secs_f64` panics on values it cannot represent,
            // and `Instant::now() + duration` (the executor's deadline) panics
            // when the deadline cannot be represented on this platform
            // (Windows QPC ticks, macOS/Linux tv_sec) — reject both here so
            // only a usable timeout ever reaches the executor.
            *secs > 0.0
                && std::time::Duration::try_from_secs_f64(*secs)
                    .is_ok_and(|duration| std::time::Instant::now().checked_add(duration).is_some())
        })
}

/// The scan-cost warning: names the fix, never blocks the search.
pub(crate) fn format_scan_cost_warning(auto_timeout: Option<f64>) -> String {
    let base = "Warning: this recursive scan of home-scale directories is unbounded in time; output caps (like `| head`) do not bound scan time. Add `timeout 60 ...`, a depth bound (`--max-depth` for rg, `-maxdepth` for find), or scan a narrower directory.";
    match auto_timeout {
        Some(secs) => {
            format!("{base} A {SCAN_TIMEOUT_ENV} scan timeout of {secs}s applies to this command.")
        }
        None => base.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The incident command (session 01a15735): a recursive grep over six
    /// home-scale trees piped to `head`, held a turn for 13 minutes.
    const INCIDENT: &str = r"grep -rli -e 'training metrics' -e 'advanced dashboard' ~/Documents ~/Desktop ~/Claude ~/environments ~/outputs ~/Downloads 2>/dev/null | grep -viE '\.(pdf|jsonl|log|txt)$' | head -10; echo done";

    #[test]
    fn the_incident_scan_shape_warns() {
        assert!(is_unbounded_home_scan(INCIDENT, None));
        // `| head` is an output cap, not a scan bound: it still warns.
        assert!(is_unbounded_home_scan(
            "grep -rl needle ~/Documents | head -10",
            None
        ));
    }

    #[test]
    fn time_or_depth_bounds_suppress_the_warning() {
        assert!(!is_unbounded_home_scan(
            "timeout 60 grep -rli -e 'training metrics' ~/Documents",
            None
        ));
        assert!(!is_unbounded_home_scan(
            "grep -rli -e 'training metrics' ~/Documents",
            Some(60.0)
        ));
        assert!(!is_unbounded_home_scan(
            "rg --max-depth 3 pattern ~/Downloads",
            None
        ));
        assert!(!is_unbounded_home_scan("rg -d 2 pattern ~/Downloads", None));
        assert!(!is_unbounded_home_scan(
            "find ~/Documents -maxdepth 2 -name '*.pdf'",
            None
        ));
    }

    #[test]
    fn project_local_and_quoted_scans_do_not_warn() {
        assert!(!is_unbounded_home_scan(
            "grep -rli pattern . | head -10",
            None
        ));
        assert!(!is_unbounded_home_scan(
            "grep -rl pattern src/ crates/",
            None
        ));
        assert!(!is_unbounded_home_scan("rg pattern .", None));
        assert!(!is_unbounded_home_scan("find . -name '*.log'", None));
        // Quoted data and comments never masquerade as scans.
        assert!(!is_unbounded_home_scan(
            "echo 'grep -r pattern ~/Documents'",
            None
        ));
        assert!(!is_unbounded_home_scan(
            "grep -rl pattern src # scan ~/Documents",
            None
        ));
    }

    #[test]
    fn unflagged_grep_stays_quiet_but_default_recursive_scanners_warn() {
        assert!(!is_unbounded_home_scan("grep -l pattern ~/Documents", None));
        assert!(is_unbounded_home_scan(
            "find ~/Downloads -name '*.pdf'",
            None
        ));
        assert!(is_unbounded_home_scan("rg pattern ~/Documents", None));
    }

    /// Regression coverage for the bot-review shapes (PR review round 1):
    /// double-quoted $HOME is a home root, filename look-alikes are not depth
    /// bounds, other env vars are not home roots, a non-positive tool timeout
    /// is no deadline, and unrepresentable scan timeouts are rejected.
    #[test]
    fn bot_review_scan_shapes() {
        // Double quotes still expand $HOME: the quoted incident form warns.
        assert!(is_unbounded_home_scan(
            "grep -rl needle \"$HOME/Documents\"",
            None
        ));
        assert!(is_unbounded_home_scan("find \"$HOME\" -name '*.pdf'", None));
        // A filename containing maxdepth is not a depth bound.
        assert!(is_unbounded_home_scan(
            "find ~/Documents -name maxdepth.txt",
            None
        ));
        // Exact home roots only: other env vars are not home-scale.
        assert!(is_unbounded_home_scan(
            "grep -rl needle $HOME/Documents",
            None
        ));
        assert!(is_unbounded_home_scan(
            "grep -rl needle ${HOME}/Downloads",
            None
        ));
        assert!(!is_unbounded_home_scan("grep -rl needle $HOMEDIR", None));
        assert!(!is_unbounded_home_scan(
            "grep -rl needle ${HOMEBREW_PREFIX}/docs",
            None
        ));
        // A non-positive tool timeout is no deadline for the executor.
        assert!(is_unbounded_home_scan(
            "grep -rl needle ~/Documents",
            Some(0.0)
        ));
        assert!(is_unbounded_home_scan(
            "grep -rl needle ~/Documents",
            Some(-5.0)
        ));
        // A quoted segment with a pipe inside is not a scan pipeline.
        assert!(!is_unbounded_home_scan(
            "echo 'example | grep -r needle ~/Documents'",
            None
        ));
        // =-joined and numeric-argument depth bounds stay quiet.
        assert!(!is_unbounded_home_scan(
            "rg --max-depth=3 needle ~/Downloads",
            None
        ));
        assert!(!is_unbounded_home_scan("rg -d2 needle ~/Downloads", None));
    }

    /// Regression coverage for the second bot-review round: double-quoted
    /// literal data (pipes, tilde look-alikes, scan text) is data, only the
    /// expansions bash actually performs inside double quotes stay live, and
    /// separator splitting stays linear on heredoc-sized commands.
    #[test]
    fn quoted_literal_data_stays_data_but_expansions_warn() {
        // A quoted pipe does not split the scanner away from its home root.
        assert!(is_unbounded_home_scan(
            "grep -rl needle \"foo|bar\" ~/Documents",
            None
        ));
        // Quoted scan text is data, not a command.
        assert!(!is_unbounded_home_scan(
            "echo \"grep -rl needle ~/Documents\"",
            None
        ));
        // Tilde inside double quotes is literal: not a home-scale root.
        assert!(!is_unbounded_home_scan(
            "grep -rl needle \"~/Documents\"",
            None
        ));
        // `$HOME` inside double quotes still expands: the scan warns.
        assert!(is_unbounded_home_scan("grep -rl needle \"$HOME\"", None));
        assert!(is_unbounded_home_scan("find \"$HOME\" -name '*.pdf'", None));
        // `$HOMEDIR` is a different variable, not a home root.
        assert!(!is_unbounded_home_scan(
            "grep -rl needle \"$HOMEDIR\"",
            None
        ));
        // A `#` inside double quotes is quoted data, not a comment.
        assert!(is_unbounded_home_scan(
            "grep -rl needle \"#\" ~/Documents",
            None
        ));
    }

    /// A heredoc-sized command splits linearly: the guard runs before every
    /// bash command, so a quadratic splitter stalled the agent on large
    /// inputs. The bound is generous (the quadratic splitter needed minutes).
    #[test]
    fn separator_splitting_is_linear_on_heredoc_sized_commands() {
        let heredoc = "x".repeat(100_000);
        let command = format!("cat <<EOF\n{heredoc}\nEOF\ngrep -rl needle ~/Documents");
        let start = std::time::Instant::now();
        let parts = split_with_separators(&command);
        let elapsed = start.elapsed();
        assert_eq!(
            parts,
            vec![
                "cat <<EOF".to_string(),
                "\n".to_string(),
                heredoc,
                "\n".to_string(),
                "EOF".to_string(),
                "\n".to_string(),
                "grep -rl needle ~/Documents".to_string(),
            ]
        );
        assert!(
            elapsed.as_secs() < 10,
            "split took {elapsed:?} on a 100k-char command"
        );
    }

    /// Regression coverage for the third bot-review round: heredoc bodies are
    /// input text (never executed by the shell), command-substitution bodies
    /// run in a subshell, and quoted numeric arguments are bounds, not data.
    #[test]
    fn heredoc_bodies_and_substitutions_scan_correctly() {
        // A heredoc body is input data, not a command: no warning, so the
        // opted-in scan timeout cannot kill an unrelated `cat` either.
        assert!(!is_unbounded_home_scan(
            "cat >example.sh <<'EOF'\ngrep -rl needle ~/Documents\nEOF",
            None
        ));
        assert!(!is_unbounded_home_scan(
            "cat <<EOF\ngrep -rl needle ~/Documents\nEOF\necho done",
            None
        ));
        // A scan inside a command substitution runs in a subshell: it warns.
        assert!(is_unbounded_home_scan(
            "echo \"$(grep -rl needle ~/Documents)\"",
            None
        ));
        assert!(is_unbounded_home_scan(
            "files=$(grep -rl needle ~/Documents); echo $files",
            None
        ));
        assert!(is_unbounded_home_scan(
            "echo \"before $(grep -rl needle ~/Documents) after\"",
            None
        ));
        // Quoted numeric depth arguments are bounds, not data.
        assert!(!is_unbounded_home_scan(
            "rg --max-depth \"3\" needle ~/Documents",
            None
        ));
        assert!(!is_unbounded_home_scan(
            "find ~/Documents -maxdepth '2'",
            None
        ));
        // Arithmetic shifts are not heredocs.
        assert!(!is_unbounded_home_scan("echo $((1<<2))", None));
    }

    #[test]
    fn scan_timeout_env_parses_only_positive_seconds() {
        let env = |v: &str| {
            let mut env = std::collections::HashMap::new();
            env.insert(SCAN_TIMEOUT_ENV.to_string(), v.to_string());
            scan_timeout_from_env(&env)
        };
        assert_eq!(env("30"), Some(30.0));
        assert_eq!(env("0.5"), Some(0.5));
        assert_eq!(env("0"), None);
        assert_eq!(env("-5"), None);
        assert_eq!(env("abc"), None);
        // A duration `Duration::from_secs_f64` cannot represent must not
        // reach the executor (it would panic instead of timing out).
        assert_eq!(env("1e30"), None);
        // A duration whose deadline `Instant::now() + duration` cannot
        // represent must not reach the executor either (checked_add fails on
        // every platform for this value; smaller values are platform-bound).
        assert_eq!(env("1e19"), None);
        assert_eq!(
            scan_timeout_from_env(&std::collections::HashMap::new()),
            None
        );
    }

    /// Runs the real tool: the incident shape carries the warning on the
    /// model-facing text, project-local scans do not, and the opted-in
    /// `PRIME_AGENT_SCAN_TIMEOUT` bounds the command. Mutates process env, so
    /// it runs as one sequential test (the golden env-bypass convention).
    #[tokio::test]
    async fn execute_bash_warns_on_unbounded_scans_and_env_timeouts_them() {
        use crate::tools::bash::{execute_bash, BashToolOptions};
        let cwd = tempfile::tempdir().expect("tempdir");
        let cwd_path = cwd.path().to_string_lossy().into_owned();

        // The warning rides the failing result; the search itself still ran.
        let result = execute_bash(
            &cwd_path,
            &BashToolOptions::default(),
            "grep -rl needle ~/pa-scan-guard-nonexistent-xyz 2>/dev/null",
            None,
            None,
            None,
            None,
        )
        .await;
        let err = result.expect_err("the missing scan root must fail");
        assert!(
            err.to_string()
                .contains("Warning: this recursive scan of home-scale directories"),
            "no scan warning on: {err}"
        );

        // A project-local scan carries no warning.
        std::fs::write(cwd.path().join("a.txt"), "needle").expect("write fixture");
        let result = execute_bash(
            &cwd_path,
            &BashToolOptions::default(),
            "grep -rl needle .",
            None,
            None,
            None,
            None,
        )
        .await
        .expect("project-local grep");
        let text = result.content[0].as_text().expect("text block");
        assert!(text.contains("a.txt"), "grep -l output lost: {text}");
        assert!(
            !text.contains("Warning: this recursive scan"),
            "unexpected warning on project-local scan: {text}"
        );

        // The opt-in env applies its timeout to the matching command and the
        // warning names it.
        std::env::set_var(SCAN_TIMEOUT_ENV, "0.5");
        let result = execute_bash(
            &cwd_path,
            &BashToolOptions::default(),
            "grep -rl needle ~/pa-scan-guard-nonexistent-xyz 2>/dev/null || sleep 2",
            None,
            None,
            None,
            None,
        )
        .await;
        std::env::remove_var(SCAN_TIMEOUT_ENV);
        let err = result.expect_err("the scan timeout must fire");
        assert!(
            err.to_string().contains("scan timeout of 0.5s"),
            "warning must name the applied scan timeout: {err}"
        );
        assert!(
            err.to_string()
                .contains("Command timed out after 0.5 seconds"),
            "the env timeout must bound the command: {err}"
        );
    }
}
