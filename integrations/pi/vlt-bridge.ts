/**
 * vlt-bridge — связка pi с vaultctl (vlt): детерминизм и guardrails автономных прогонов.
 *
 * Включается только когда процесс запущен vaultctl (env VAULTCTL_RUN=1, runner ставит
 * его сам рядом с OBSIDIAN_VAULT_PATH). В обычных сессиях pi расширение инертно.
 *
 * UI недоступен: vlt гоняет агента в -p/--mode json, где ctx.ui — no-op, поэтому все
 * правила безусловные — блок без вопроса, причина уходит модели в toolResult, чтобы
 * она скорректировала действие.
 *
 * Что делает:
 *  1. Перед первым ответом модели инжектит инструкцию прочитать навык obsidian и
 *     работать строго по нему. Прогрессивный disclosure (описание навыка в системном
 *     промте) не гарантирует, что модель откроет SKILL.md; для unattended-прогона
 *     пропуск навыка — тихий полусбой: vlt получит код 0 и пустой результат.
 *  2. Блокирует записи модели вне vault (и временных папок) и заведомо опасные
 *     shell-команды.
 *
 * Установка: скопировать в ~/.pi/agent/extensions/vlt-bridge.ts — pi грузит TS
 * напрямую (jiti). Источник правды — integrations/pi/vlt-bridge.ts в репозитории
 * vaultctl; после правки перекопировать.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import * as os from "node:os";
import * as path from "node:path";

interface BeforeAgentStartEvent {
	prompt?: string;
}

interface ToolCallEvent {
	toolName: string;
	input: Record<string, unknown>;
}

/** Заведомо опасные shell-команды: регулярка + человекочитаемая причина. */
const DANGEROUS_SHELL: Array<[RegExp, string]> = [
	[/\brm\s+(-[a-zA-Z]+\s+)*\/(\s|$|\*)/i, "удаление корня файловой системы"],
	[/\brm\s+(-[a-zA-Z]+\s+)+(~|\$HOME)(\s|\/|$)/i, "удаление домашней папки"],
	[/\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b/i, "исполнение скачанного скрипта"],
	[/\b(curl|wget)\b[^|;&]*\|\s*powershell\b/i, "исполнение скачанного скрипта"],
	[/\bdel\s+\/[sqf]\b/i, "массовое удаление по Windows-семантике"],
	[/\bformat\s+[a-z]:/i, "форматирование диска"],
	[
		/\bRemove-Item\b[^|;&]*-Recurse[^|;&]*-Force\s+([A-Za-z]:\\|~|\$HOME)/i,
		"рекурсивное удаление диска или домашней папки",
	],
];

/** Куда модели разрешено писать: vault (cwd прогона), OBSIDIAN_VAULT_PATH, temp. */
function allowedWriteTargets(): string[] {
	const targets = [process.cwd()];
	const vault = process.env.OBSIDIAN_VAULT_PATH;
	if (vault) targets.push(vault);
	targets.push(os.tmpdir());
	return targets.map((dir) => path.resolve(dir));
}

function isInside(child: string, parent: string): boolean {
	const rel = path.relative(parent, child);
	return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/** Причина блокировки пути записи или null, если путь разрешён. */
function checkWritePath(raw: unknown): string | null {
	if (typeof raw !== "string" || !raw.trim()) return null;
	const target = path.resolve(raw);
	for (const base of allowedWriteTargets()) {
		if (isInside(target, base)) return null;
	}
	return (
		`vaultctl: запись разрешена только внутрь vault или во временную папку, ` +
		`а не в ${target}. Промежуточные файлы — во временной папке системы.`
	);
}

/** Операция навыка по префиксу задачи, который vaultctl кладёт в argv. */
function detectOperation(prompt: string): string {
	const trimmed = prompt.trimStart();
	if (/^clip\b/i.test(trimmed)) return "клиппинг URL или статьи";
	if (/^NameRegister\b/i.test(trimmed)) return "NameRegister";
	if (/^task\b/i.test(trimmed))
		return (
			"запись проектной задачи: сформулировать и записать задачу по навыку; " +
			"саму описанную работу не выполнять"
		);
	return "общая задача по vault";
}

function skillInstruction(prompt: string): string {
	return [
		"Это автономный запуск от vaultctl: результат уйдёт в Obsidian vault без проверки человеком.",
		"Прежде чем действовать, прочитай навык obsidian (SKILL.md) и выполняй задачу строго по его инструкциям.",
		`Операция: ${detectOperation(prompt)}.`,
		"Целевые заметки создавай и меняй только внутри vault (OBSIDIAN_VAULT_PATH); промежуточные файлы — во временной папке системы.",
	].join(" ");
}

async function onBeforeAgentStart(event: unknown) {
	const { prompt } = (event ?? {}) as BeforeAgentStartEvent;
	return {
		message: {
			customType: "vlt-bridge",
			content: skillInstruction(prompt ?? ""),
			display: true,
		},
	};
}

async function onToolCall(event: unknown) {
	const { toolName, input } = (event ?? { input: {} }) as ToolCallEvent;

	if (toolName === "write" || toolName === "edit") {
		const violation = checkWritePath(input.path);
		if (violation) return { block: true, reason: violation };
		return undefined;
	}

	if (toolName === "bash" || toolName === "powershell") {
		const command = typeof input.command === "string" ? input.command : "";
		for (const [pattern, label] of DANGEROUS_SHELL) {
			if (pattern.test(command)) {
				return {
					block: true,
					reason:
						`vaultctl: заблокировано опасное действие — ${label}. ` +
						`Команда: ${command.slice(0, 120)}`,
					};
			}
		}
	}

	return undefined;
}

export default function (pi: ExtensionAPI) {
	// Вне vlt-прогонов расширение инертно: интерактивные сессии pi не должны
	// получать неожиданные блокировки и подсказки.
	if (process.env.VAULTCTL_RUN !== "1") return;

	pi.on("before_agent_start", onBeforeAgentStart);
	pi.on("tool_call", onToolCall);
}

/** Тестам вне pi: хэндлеры чистые, env читается на вызове. */
export const _internals = { onBeforeAgentStart, onToolCall, checkWritePath, detectOperation };
