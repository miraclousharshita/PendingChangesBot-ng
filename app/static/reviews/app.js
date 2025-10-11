const { createApp, reactive, computed, onMounted, watch } = Vue;

function parseTextarea(value) {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function formatDateTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

createApp({
  setup() {
    const initialElement = document.getElementById("initial-wikis");
    const initialData = initialElement ? JSON.parse(initialElement.textContent) : [];

    const configurationStorageKey = "configurationOpen";
    const selectedWikiStorageKey = "selectedWikiId";
    const sortOrderStorageKey = "pendingSortOrder";
    const pageDisplayLimit = 100;

    function loadFromStorage(key) {
      if (typeof window === "undefined") {
        return null;
      }
      try {
        return window.localStorage.getItem(key);
      } catch (error) {
        return null;
      }
    }

    function saveToStorage(key, value) {
      if (typeof window === "undefined") {
        return;
      }
      try {
        if (value === null || typeof value === "undefined") {
          window.localStorage.removeItem(key);
        } else {
          window.localStorage.setItem(key, String(value));
        }
      } catch (error) {
        // Ignore storage errors.
      }
    }

    function loadConfigurationOpen() {
      return loadFromStorage(configurationStorageKey) === "true";
    }

    function persistConfigurationOpen(value) {
      saveToStorage(configurationStorageKey, value ? "true" : "false");
    }

    function loadSelectedWikiId(wikis) {
      if (!Array.isArray(wikis) || !wikis.length) {
        return "";
      }
      const storedValue = loadFromStorage(selectedWikiStorageKey);
      if (storedValue === null) {
        return wikis[0].id;
      }
      const parsedValue = Number(storedValue);
      if (!Number.isNaN(parsedValue)) {
        const matchedWiki = wikis.find((wiki) => wiki.id === parsedValue || Number(wiki.id) === parsedValue);
        if (matchedWiki) {
          return matchedWiki.id;
        }
      }
      return wikis[0].id;
    }

    function persistSelectedWikiId(value) {
      if (value === "") {
        saveToStorage(selectedWikiStorageKey, null);
        return;
      }
      saveToStorage(selectedWikiStorageKey, value);
    }

    function loadSortOrder() {
      const storedValue = loadFromStorage(sortOrderStorageKey);
      if (storedValue === "newest" || storedValue === "oldest" || storedValue === "random") {
        return storedValue;
      }
      return "newest";
    }

    function persistSortOrder(value) {
      saveToStorage(sortOrderStorageKey, value);
    }

    function getPendingTimestamp(page) {
      if (!page || !page.pending_since) {
        return 0;
      }
      const timestamp = new Date(page.pending_since).getTime();
      return Number.isNaN(timestamp) ? 0 : timestamp;
    }

    function shufflePages(pages) {
      const shuffled = [...pages];
      for (let index = shuffled.length - 1; index > 0; index -= 1) {
        const swapIndex = Math.floor(Math.random() * (index + 1));
        [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
      }
      return shuffled;
    }

    function sortPages(pages, order) {
      if (!Array.isArray(pages)) {
        return [];
      }
      if (order === "random") {
        return shufflePages(pages);
      }
      const sorted = [...pages];
      sorted.sort((first, second) => {
        const firstTimestamp = getPendingTimestamp(first);
        const secondTimestamp = getPendingTimestamp(second);
        if (order === "oldest") {
          return firstTimestamp - secondTimestamp;
        }
        return secondTimestamp - firstTimestamp;
      });
      return sorted;
    }

    const state = reactive({
      wikis: initialData,
      selectedWikiId: initialData.length ? loadSelectedWikiId(initialData) : "",
      sortOrder: loadSortOrder(),
      pages: [],
      loading: false,
      error: "",
      configurationOpen: loadConfigurationOpen(),
      reviewResults: {},
      runningReviews: {},
      runningBulkReview: false,
      searchQuery: "",
    });

    const forms = reactive({
      blockingCategories: "",
      autoApprovedGroups: "",
    });

    const currentWiki = computed(() =>
      state.wikis.find((wiki) => wiki.id === state.selectedWikiId) || null,
    );

    function matchesSearchQuery(page) {
      if (!state.searchQuery || state.searchQuery.trim() === "") {
        return true;
      }
      const query = state.searchQuery.toLowerCase().trim();

      // Check page title
      if (page.title) {
        const formattedTitle = formatTitle(page.title).toLowerCase().replace(/\s+/g, ' ');
        const normalizedQuery = query.replace(/\s+/g, ' ');
        if (formattedTitle.includes(normalizedQuery)) {
          return true;
        }
      }

      // Check revisions
      if (Array.isArray(page.revisions)) {
        for (const revision of page.revisions) {
          // Check timestamp
          if (revision.timestamp) {
            // Check raw timestamp
            if (revision.timestamp.toLowerCase().includes(query)) {
              return true;
            }
            // Check formatted timestamp as displayed in UI
            const formattedTimestamp = formatDateTime(revision.timestamp).toLowerCase();
            if (formattedTimestamp.includes(query)) {
              return true;
            }
          }
          // Check user_name
          if (revision.user_name && revision.user_name.toLowerCase().includes(query)) {
            return true;
          }
          // Check comment
          if (revision.comment && revision.comment.toLowerCase().includes(query)) {
            return true;
          }
          // Check change_tags
          if (Array.isArray(revision.change_tags)) {
            for (const tag of revision.change_tags) {
              if (tag && tag.toLowerCase().includes(query)) {
                return true;
              }
            }
          }
        }
      }

      return false;
    }

    const filteredPages = computed(() => {
      if (!state.searchQuery || state.searchQuery.trim() === "") {
        return state.pages;
      }
      return state.pages.filter((page) => matchesSearchQuery(page));
    });

    const visiblePages = computed(() => filteredPages.value.slice(0, pageDisplayLimit));

    const hasMorePages = computed(() => filteredPages.value.length > pageDisplayLimit);

    function syncForms() {
      if (!currentWiki.value) {
        forms.blockingCategories = "";
        forms.autoApprovedGroups = "";
        return;
      }
      forms.blockingCategories = (currentWiki.value.configuration.blocking_categories || []).join("\n");
      forms.autoApprovedGroups = (currentWiki.value.configuration.auto_approved_groups || []).join("\n");
    }

    async function apiRequest(url, options = {}) {
      state.error = "";
      try {
        const response = await fetch(url, options);
        if (!response.ok) {
          let message = response.statusText;
          try {
            const data = await response.json();
            if (data && data.error) {
              message = data.error;
            }
          } catch (error) {
            // Ignore JSON parsing errors.
          }
          throw new Error(message || "Unknown error");
        }
        return response.json();
      } catch (error) {
        state.error = error.message || "Request failed";
        throw error;
      }
    }

    async function fetchRevisionsForPage(wikiId, pageId) {
      try {
        const data = await apiRequest(`/api/wikis/${wikiId}/pages/${pageId}/revisions/`);
        return data.revisions || [];
      } catch (error) {
        return [];
      }
    }

    function resetReviewState() {
      state.reviewResults = {};
      state.runningReviews = {};
    }

    function setRunning(pageId, value) {
      state.runningReviews = { ...state.runningReviews, [pageId]: Boolean(value) };
    }

    function setReviewResults(pageId, results) {
      state.reviewResults = { ...state.reviewResults, [pageId]: results };
    }

    async function loadPending() {
      resetReviewState();
      if (!state.selectedWikiId) {
        state.pages = [];
        return;
      }
      state.loading = true;
      try {
        const wikiId = state.selectedWikiId;
        const data = await apiRequest(`/api/wikis/${wikiId}/pending/`);
        const pagesWithRevisions = await Promise.all(
          (data.pages || []).map(async (page) => {
            let revisions = Array.isArray(page.revisions) ? page.revisions : [];
            if (!revisions.length) {
              revisions = await fetchRevisionsForPage(wikiId, page.pageid);
            }
            return {
              ...page,
              revisions,
            };
          }),
        );
        if (wikiId === state.selectedWikiId) {
          state.pages = sortPages(pagesWithRevisions, state.sortOrder);
        }
      } catch (error) {
        state.pages = [];
      } finally {
        state.loading = false;
      }
    }

    async function refresh() {
      if (!state.selectedWikiId) {
        return;
      }
      state.loading = true;
      try {
        await apiRequest(`/api/wikis/${state.selectedWikiId}/refresh/`, {
          method: "POST",
        });
        await loadPending();
      } finally {
        state.loading = false;
      }
    }

    async function clearCache() {
      if (!state.selectedWikiId) {
        return;
      }
      state.loading = true;
      try {
        await apiRequest(`/api/wikis/${state.selectedWikiId}/clear/`, {
          method: "POST",
        });
        state.pages = [];
        resetReviewState();
      } finally {
        state.loading = false;
      }
    }

    async function saveConfiguration() {
      if (!state.selectedWikiId) {
        return;
      }
      const payload = {
        blocking_categories: parseTextarea(forms.blockingCategories),
        auto_approved_groups: parseTextarea(forms.autoApprovedGroups),
      };
      try {
        const data = await apiRequest(`/api/wikis/${state.selectedWikiId}/configuration/`, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        });
        const wikiIndex = state.wikis.findIndex((wiki) => wiki.id === state.selectedWikiId);
        if (wikiIndex >= 0) {
          state.wikis[wikiIndex].configuration = data;
        }
        syncForms();
      } catch (error) {
        // Error already handled in apiRequest.
      }
    }

    function formatDate(value) {
      return formatDateTime(value);
    }

    function formatTitle(title) {
      if (!title) {
        return "";
      }
      return title.replace(/_/g, " ");
    }

    function getWikiOrigin() {
      const wiki = currentWiki.value;
      if (!wiki || !wiki.api_endpoint) {
        return "";
      }
      try {
        const apiUrl = new URL(wiki.api_endpoint);
        return apiUrl.origin;
      } catch (error) {
        return "";
      }
    }

    function buildLatestRevisionUrl(page) {
      if (!page || !page.title) {
        return "";
      }
      const normalizedTitle = page.title.replace(/ /g, "_");
      const encodedTitle = encodeURIComponent(normalizedTitle);
      const origin = getWikiOrigin();
      if (origin) {
        return `${origin}/wiki/${encodedTitle}`;
      }
      return `/wiki/${encodedTitle}`;
    }

    function buildRevisionDiffUrl(page, revision) {
      if (!revision || !revision.revid) {
        return "";
      }
      const params = new URLSearchParams({ diff: revision.revid });
      const parentId = Number(revision.parentid);
      if (!Number.isNaN(parentId) && parentId > 0) {
        params.set("oldid", parentId);
      } else if (page && page.stable_revid) {
        const stableId = Number(page.stable_revid);
        if (!Number.isNaN(stableId) && stableId > 0) {
          params.set("oldid", stableId);
        }
      }
      const origin = getWikiOrigin();
      const baseUrl = origin ? `${origin}/w/index.php` : "/w/index.php";
      return `${baseUrl}?${params.toString()}`;
    }

    function buildUserContributionsUrl(revision) {
      if (!revision || !revision.user_name) {
        return "";
      }
      const origin = getWikiOrigin();
      const normalized = revision.user_name.replace(/ /g, "_");
      const encoded = encodeURIComponent(normalized);
      if (origin) {
        return `${origin}/wiki/Special:Contributions/${encoded}`;
      }
      return `/wiki/Special:Contributions/${encoded}`;
    }

    function buildFlaggedRevsUrl(specialPage) {
      const origin = getWikiOrigin();
      if (!origin) {
        return "";
      }
      return `${origin}/wiki/Special:${specialPage}?uselang=en`;
    }

    function toggleConfiguration() {
      state.configurationOpen = !state.configurationOpen;
    }

    async function runAutoreview(page) {
      if (!page || !state.selectedWikiId) {
        return;
      }
      const pageId = page.pageid;
      setRunning(pageId, true);
      try {
        const data = await apiRequest(
          `/api/wikis/${state.selectedWikiId}/pages/${pageId}/autoreview/`,
          {
            method: "POST",
          },
        );
        const mapping = {};
        (data.results || []).forEach((entry) => {
          if (entry && typeof entry.revid !== "undefined") {
            mapping[entry.revid] = entry;
          }
        });
        setReviewResults(pageId, mapping);
      } catch (error) {
        // Errors are surfaced via apiRequest state handling.
      } finally {
        setRunning(pageId, false);
      }
    }

    async function runAutoreviewAllVisible() {
      if (!state.selectedWikiId || state.runningBulkReview) {
        return;
      }
      const pages = visiblePages.value || [];
      if (!pages.length) {
        return;
      }
      const wikiId = state.selectedWikiId;
      state.runningBulkReview = true;
      try {
        for (const page of pages) {
          if (!page) {
            continue;
          }
          if (state.selectedWikiId !== wikiId) {
            break;
          }
          await runAutoreview(page);
        }
      } finally {
        state.runningBulkReview = false;
      }
    }

    function getRevisionReview(page, revision) {
      if (!page || !revision) {
        return null;
      }
      const pageResults = state.reviewResults[page.pageid];
      if (!pageResults) {
        return null;
      }
      return pageResults[revision.revid] || null;
    }

    function isAutoreviewRunning(page) {
      if (!page) {
        return false;
      }
      return Boolean(state.runningReviews[page.pageid]);
    }

    function formatTestStatus(status) {
      if (status === "ok") {
        return "OK";
      }
      if (status === "fail") {
        return "FAIL";
      }
      if (status === "not_ok") {
        return "Neutral";
      }
      return status || "";
    }

    function statusTagClass(status) {
      if (status === "ok") {
        return "is-success";
      }
      if (status === "fail") {
        return "is-danger";
      }
      if (status === "not_ok") {
        return "is-warning";
      }
      return "is-light";
    }

    function formatDecision(decision) {
      if (!decision) {
        return "";
      }
      const label = decision.label || decision.status || "";
      const reason = decision.reason ? ` – ${decision.reason}` : "";
      const base = `${label}${reason}`.trim();
      if (!base) {
        return "(dry-run)";
      }
      return `${base} (dry-run)`;
    }

    watch(
      () => state.configurationOpen,
      (newValue) => {
        persistConfigurationOpen(newValue);
      },
      { immediate: true },
    );

    watch(
      () => state.selectedWikiId,
      (newValue) => {
        persistSelectedWikiId(newValue);
        resetReviewState();
      },
      { immediate: true },
    );

    watch(
      () => state.sortOrder,
      (newValue) => {
        state.pages = sortPages(state.pages, newValue);
        persistSortOrder(newValue);
      },
      { immediate: true },
    );

    watch(currentWiki, () => {
      syncForms();
      loadPending();
    }, { immediate: true });

    onMounted(() => {
      syncForms();
    });

    return {
      state,
      forms,
      currentWiki,
      filteredPages,
      visiblePages,
      hasMorePages,
      pageDisplayLimit,
      refresh,
      clearCache,
      saveConfiguration,
      loadPending,
      formatDate,
      toggleConfiguration,
      formatTitle,
      buildLatestRevisionUrl,
      buildRevisionDiffUrl,
      buildUserContributionsUrl,
      buildFlaggedRevsUrl,
      runAutoreview,
      runAutoreviewAllVisible,
      getRevisionReview,
      isAutoreviewRunning,
      formatTestStatus,
      statusTagClass,
      formatDecision,
    };
  },
}).mount("#app");
