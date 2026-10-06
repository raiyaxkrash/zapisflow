// Private portfolio media uses the same session + tenant header as other API reads.
export function bindPortfolioMedia(root, api) {
  const win = root.ownerDocument.defaultView;
  const urls = new Set();
  let disposed = false;
  async function load(node) {
    try {
      const blob = await api.image(node.dataset.privateImage);
      if (disposed || !node.isConnected) return;
      const url = win.URL.createObjectURL(blob);
      urls.add(url);
      node.src = url;
    } catch {
      if (!disposed && node.isConnected) {
        node.alt = "Изображение временно недоступно";
        node
          .closest("figure")
          ?.insertAdjacentHTML(
            "beforeend",
            '<p class="sub" role="status">Не удалось загрузить изображение. Откройте раздел ещё раз.</p>',
          );
      }
    }
  }
  const observer = win.IntersectionObserver
    ? new win.IntersectionObserver(
        (entries) => {
          for (const entry of entries)
            if (entry.isIntersecting) {
              observer.unobserve(entry.target);
              load(entry.target);
            }
        },
        { rootMargin: "100px" },
      )
    : null;
  for (const node of root.querySelectorAll("[data-private-image]")) {
    if (observer) observer.observe(node);
    else load(node);
  }
  return () => {
    disposed = true;
    observer?.disconnect();
    for (const url of urls) win.URL.revokeObjectURL(url);
  };
}
