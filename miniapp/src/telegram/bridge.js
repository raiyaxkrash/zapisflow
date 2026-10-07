// Capability and version gates keep older Telegram clients functional.
export function createTelegramBridge(tg,win=window) {
  const supports = version => !tg?.isVersionAtLeast || tg.isVersionAtLeast(version);
  let primaryCallback,backCallback;
  return {
    setBackVisible(visible){if(supports('6.1'))tg?.BackButton?.[visible?'show':'hide']?.();},
    onBack(callback){if(backCallback)tg?.BackButton?.offClick?.(backCallback);backCallback=callback;if(supports('6.1'))tg?.BackButton?.onClick?.(callback);},
    notify(kind='success'){if(supports('6.1')&&['success','error','warning'].includes(kind))tg?.HapticFeedback?.notificationOccurred?.(kind);},
    setPrimaryAction(action){const button=tg?.MainButton;if(!button)return false;if(primaryCallback)button.offClick?.(primaryCallback);primaryCallback=null;if(!action){button.hide?.();return true;}button.setText?.(action.label);primaryCallback=action.onClick;button.onClick?.(primaryCallback);button[action.disabled?'disable':'enable']?.();button.show?.();return true;},
    syncChrome(){if(!supports('6.1'))return;const css=win.getComputedStyle?.(win.document.documentElement);const bg=css?.getPropertyValue('--zf-bg').trim();if(/^#[a-f0-9]{6}$/i.test(bg||'')){tg?.setHeaderColor?.(supports('6.9') ? bg : 'bg_color');tg?.setBackgroundColor?.(bg);}},
    dispose(){if(primaryCallback)tg?.MainButton?.offClick?.(primaryCallback);tg?.MainButton?.hide?.();if(backCallback)tg?.BackButton?.offClick?.(backCallback);},
  };
}
