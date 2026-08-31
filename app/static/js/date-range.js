export function kstCalendarDate(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function presetCalendarRange(value, now = new Date()) {
  const todayValue = kstCalendarDate(now);
  const [year, month, day] = todayValue.split("-").map(Number);
  const start = new Date(Date.UTC(year, month - 1, day));
  const end = new Date(Date.UTC(year, month - 1, day));
  if (value === "month") start.setUTCDate(start.getUTCDate() - 30);
  if (value === "yesterday") {
    start.setUTCDate(start.getUTCDate() - 1);
    end.setUTCDate(end.getUTCDate() - 1);
  }
  return [kstCalendarDate(start), kstCalendarDate(end)];
}

function byId(root, id) {
  if (!id) return null;
  if (typeof root.getElementById === "function") return root.getElementById(id);
  return root.querySelector(`#${id}`);
}

export function bindDateRanges({
  root = document,
  configs = [],
  closeOnOutside = false,
  closeOnEscape = false,
} = {}) {
  const configById = new Map(configs.map((config) => [config.selectId, config]));

  function closePopovers(exceptSelectId = null) {
    root.querySelectorAll("[data-range-popover]").forEach((popover) => {
      if (popover.dataset.rangePopover !== exceptSelectId) popover.hidden = true;
    });
  }

  function sync(selectId) {
    const config = configById.get(selectId);
    const select = byId(root, selectId);
    const control = root.querySelector(`[data-range-control="${selectId}"]`);
    if (!config || !select || !control) return;
    control.querySelectorAll("[data-range-value]").forEach((button) => {
      const active = button.dataset.rangeValue === select.value;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const start = byId(root, config.startId)?.value || "";
    const end = byId(root, config.endId)?.value || "";
    const customButton = control.querySelector('[data-range-value="custom"]');
    if (customButton) {
      customButton.textContent = select.value === "custom" && start && end
        ? `📅 ${start} ~ ${end} ▾`
        : "📅 직접 입력 ▾";
    }
  }

  function setPreset(config, value) {
    if (!config.setPresetInputs || value === "custom") return;
    const startInput = byId(root, config.startId);
    const endInput = byId(root, config.endId);
    if (!startInput || !endInput) return;
    if (value === "all") {
      // 전체 기간 — 날짜 조건 없음
      startInput.value = "";
      endInput.value = "";
      return;
    }
    const [start, end] = presetCalendarRange(value);
    startInput.value = start;
    endInput.value = end;
  }

  function openPopover(config) {
    closePopovers(config.selectId);
    const popover = root.querySelector(`[data-range-popover="${config.selectId}"]`);
    if (!popover) return;
    popover.hidden = false;
    window.requestAnimationFrame(() => {
      const startInput = byId(root, config.startId);
      const endInput = byId(root, config.endId);
      const target = config.focusFirstEmpty === false
        ? startInput
        : [startInput, endInput].find((input) => input && !input.value) || startInput;
      target?.focus();
    });
  }

  configs.forEach((config) => {
    const select = byId(root, config.selectId);
    const control = root.querySelector(`[data-range-control="${config.selectId}"]`);
    if (!select || !control) return;
    setPreset(config, select.value);
    control.querySelectorAll("[data-range-value]").forEach((button) => {
      button.addEventListener("click", () => {
        select.value = button.dataset.rangeValue;
        setPreset(config, select.value);
        if (config.dispatchChange) {
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        sync(config.selectId);
        if (select.value === "custom") {
          openPopover(config);
        } else {
          closePopovers();
          config.onPreset?.();
        }
      });
    });
    [config.startId, config.endId].forEach((inputId) => {
      byId(root, inputId)?.addEventListener("change", () => sync(config.selectId));
    });
    select.addEventListener("change", () => sync(config.selectId));
    sync(config.selectId);
  });

  root.querySelectorAll("[data-range-apply]").forEach((button) => {
    button.addEventListener("click", (event) => {
      const config = configById.get(button.dataset.rangeApply);
      if (!config) return;
      if (config.stopApplyPropagation) event.stopPropagation();
      const startInput = byId(root, config.startId);
      const endInput = byId(root, config.endId);
      if (!startInput?.value || !endInput?.value) {
        config.onInvalid?.({ startInput, endInput });
        (startInput?.value ? endInput : startInput)?.focus();
        return;
      }
      sync(config.selectId);
      closePopovers();
      config.onApply?.();
    });
  });

  if (closeOnOutside) {
    document.addEventListener("pointerdown", (event) => {
      if (!event.target.closest(".range-picker")) closePopovers();
    });
  }
  if (closeOnEscape) {
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closePopovers();
    });
  }

  return { closePopovers, sync };
}
