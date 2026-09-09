import { useCallback, useEffect, useRef, useState } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import { Edit2, Plus, Search, Trash2, UserPlus, Users, X, ChevronLeft, ChevronRight, Minus } from "lucide-react";

interface ListItem {
  id: number;
  name: string;
  description: string | null;
  contact_count: number;
  created_at: string;
  updated_at: string;
}

const displayName = (contact: Contact) =>
  [contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
  contact.business_name ||
  contact.phone_number;

const PAGE_SIZE = 50;

export default function ListsPage() {
  const [lists, setLists] = useState<ListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  const [viewListId, setViewListId] = useState<number | null>(null);
  const [viewListName, setViewListName] = useState("");

  // Members view is server-paginated + searchable, so a list with 50k
  // contacts renders the same 50 rows as one with 5.
  const [listContacts, setListContacts] = useState<Contact[]>([]);
  const [listTotal, setListTotal] = useState(0);
  const [listMatchingTotal, setListMatchingTotal] = useState(0);
  const [memberPage, setMemberPage] = useState(1);
  const [memberSearch, setMemberSearch] = useState("");
  const [memberSearchInput, setMemberSearchInput] = useState("");
  const [listLoading, setListLoading] = useState(false);
  const searchTimer = useRef<number | null>(null);

  const [showAddContacts, setShowAddContacts] = useState(false);
  // Add-contacts picker: searches the server, never loads every contact.
  const [addItems, setAddItems] = useState<Contact[]>([]);
  const [addTotal, setAddTotal] = useState(0);
  const [addLoading, setAddLoading] = useState(false);
  const [addPage, setAddPage] = useState(1);
  const [addQuery, setAddQuery] = useState("");
  const [selectedToAdd, setSelectedToAdd] = useState<Set<number>>(new Set());
  const addTimer = useRef<number | null>(null);

  const [selectedInList, setSelectedInList] = useState<Set<number>>(new Set());
  // "All N matching selected": every member matching the current search
  // across ALL pages is part of the action (server-scoped, no id listing).
  const [allMatchingInList, setAllMatchingInList] = useState(false);
  const [busyRow, setBusyRow] = useState<string | null>(null);
  const [removingBulk, setRemovingBulk] = useState(false);
  const [deletingPermanently, setDeletingPermanently] = useState(false);
  const [deletingList, setDeletingList] = useState(false);
  const [addingToList, setAddingToList] = useState(false);

  useEffect(() => {
    loadLists();
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
      if (addTimer.current) window.clearTimeout(addTimer.current);
    };
  }, []);

  const loadLists = async () => {
    try {
      setLoading(true);
      setError(null);
      const { data } = await api.get("/lists/", { params: { per_page: 100 } });
      setLists(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load lists");
    } finally {
      setLoading(false);
    }
  };

  // ---- Members of the open list ------------------------------------------

  const loadListContacts = useCallback(async (listId: number, page: number, search: string, quiet = false) => {
    if (!quiet) setListLoading(true);
    try {
      const { data } = await api.get<PaginatedResponse<Contact>>(`/lists/${listId}/contacts`, {
        params: { page, per_page: PAGE_SIZE, search: search.trim() || undefined },
      });
      setListContacts(data.items);
      // listTotal is the REAL size of the list (used for the header + the
      // "delete list with all numbers" warning); the matching total can be
      // smaller when a search is active.
      if (!search.trim()) setListTotal(data.total);
      setListMatchingTotal(data.total);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to load list contacts");
    } finally {
      if (!quiet) setListLoading(false);
    }
  }, []);

  const openListEditor = async (list: ListItem) => {
    setViewListId(list.id);
    setViewListName(list.name);
    setListContacts([]);
    setListTotal(list.contact_count);
    setListMatchingTotal(list.contact_count);
    setMemberPage(1);
    setMemberSearch("");
    setMemberSearchInput("");
    setShowAddContacts(false);
    setSelectedToAdd(new Set());
    setSelectedInList(new Set());
    setAllMatchingInList(false);
    await loadListContacts(list.id, 1, "");
  };

  const closeListEditor = () => {
    setViewListId(null);
    setShowAddContacts(false);
    setSelectedToAdd(new Set());
    setSelectedInList(new Set());
    setAllMatchingInList(false);
  };

  const applyMemberSearch = (q: string) => {
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      setMemberSearch(q);
      setMemberPage(1);
      setSelectedInList(new Set());
      setAllMatchingInList(false);
      if (viewListId) loadListContacts(viewListId, 1, q);
    }, 300);
  };

  // Reload the current view after any change, snapping back a page when the
  // last row of the last page was deleted.
  const reloadMembers = async () => {
    if (!viewListId) return;
    const fetchPage = async (page: number) =>
      api.get<PaginatedResponse<Contact>>(`/lists/${viewListId}/contacts`, {
        params: { page, per_page: PAGE_SIZE, search: memberSearch.trim() || undefined },
      });
    let page = memberPage;
    let { data } = await fetchPage(page);
    if (data.items.length === 0 && page > 1) {
      page -= 1;
      setMemberPage(page);
      data = (await fetchPage(page)).data;
    }
    setListContacts(data.items);
    if (!memberSearch.trim()) setListTotal(data.total);
    setListMatchingTotal(data.total);
  };

  const pageAllSelected =
    listContacts.length > 0 && selectedInList.size === listContacts.length && !allMatchingInList;
  const selectionCount = allMatchingInList ? listMatchingTotal : selectedInList.size;
  const selectedLabel = allMatchingInList
    ? `All ${listMatchingTotal} matching`
    : `${selectedInList.size} selected`;

  const toggleInList = (contactId: number) => {
    if (allMatchingInList) {
      const next = new Set(listContacts.map((c) => c.id));
      next.delete(contactId);
      setSelectedInList(next);
      setAllMatchingInList(false);
      return;
    }
    const next = new Set(selectedInList);
    next.has(contactId) ? next.delete(contactId) : next.add(contactId);
    setSelectedInList(next);
  };

  // Three states: 50 on this page -> all N matching (every page) -> clear.
  const toggleAllInList = () => {
    if (allMatchingInList) {
      setSelectedInList(new Set());
      setAllMatchingInList(false);
      return;
    }
    if (pageAllSelected) {
      if (listMatchingTotal > listContacts.length) setAllMatchingInList(true);
      else setSelectedInList(new Set());
    } else {
      setSelectedInList(new Set(listContacts.map((c) => c.id)));
    }
  };

  const clearSelection = () => {
    setSelectedInList(new Set());
    setAllMatchingInList(false);
  };

  const handleCreate = async () => {
    if (!newName.trim()) {
      toast.error("Enter a list name");
      return;
    }
    try {
      await api.post("/lists/", null, { params: { name: newName.trim() } });
      toast.success("List created");
      setNewName("");
      setShowCreate(false);
      loadLists();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create list");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this list? Contacts themselves will not be deleted.")) return;
    try {
      await api.delete(`/lists/${id}`);
      toast.success("List deleted");
      if (viewListId === id) closeListEditor();
      loadLists();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete list");
    }
  };

  const handleRename = async (id: number) => {
    if (!editName.trim()) return;
    try {
      await api.put(`/lists/${id}`, null, { params: { name: editName.trim() } });
      toast.success("List renamed");
      setEditingId(null);
      if (viewListId === id) setViewListName(editName.trim());
      loadLists();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to rename list");
    }
  };

  // ---- Removal / deletion (fast at any size: server-scoped) --------------

  const bulkPayload = () => ({
    contact_ids: allMatchingInList ? [] : [...selectedInList],
    scope: allMatchingInList ? "all" : "ids",
    search: allMatchingInList && memberSearch.trim() ? memberSearch.trim() : undefined,
  });

  const handleBulkRemoveFromList = async () => {
    if (!viewListId || selectionCount === 0) return;
    const count = selectionCount;
    if (!window.confirm(`Remove ${count} contact${count === 1 ? "" : "s"} from ${viewListName}? The numbers will NOT be deleted.`)) return;
    try {
      setRemovingBulk(true);
      await api.post(`/lists/${viewListId}/contacts/remove`, bulkPayload());
      toast.success(`${count} removed from list`);
      clearSelection();
      await Promise.all([reloadMembers(), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to remove contacts");
    } finally {
      setRemovingBulk(false);
    }
  };

  const handlePermanentDeleteInList = async () => {
    if (!viewListId || selectionCount === 0) return;
    const count = selectionCount;
    if (!window.confirm(
      allMatchingInList && memberSearch.trim()
        ? `Permanently delete all ${count} phone numbers matching “${memberSearch.trim()}” in ${viewListName}?\n\nThis deletes the contacts, their messages and all related history. It cannot be undone.`
        : `Permanently delete ${count} phone number${count === 1 ? "" : "s"} from ${viewListName}?\n\nThis permanently deletes the contacts, their messages and all related history. This cannot be undone.`
    )) return;
    try {
      setDeletingPermanently(true);
      const { data } = await api.post(`/lists/${viewListId}/contacts/delete`, bulkPayload());
      toast.success(`${data.deleted ?? count} phone number${(data.deleted ?? count) === 1 ? "" : "s"} permanently deleted`);
      clearSelection();
      await Promise.all([reloadMembers(), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete contacts");
    } finally {
      setDeletingPermanently(false);
    }
  };

  const handleRemoveContact = async (contact: Contact) => {
    if (!viewListId) return;
    if (!window.confirm(`Remove ${displayName(contact)} from ${viewListName}? The contact will not be deleted.`)) return;
    try {
      setBusyRow(`remove-${contact.id}`);
      await api.post(`/lists/${viewListId}/contacts/remove`, { contact_ids: [contact.id] });
      toast.success("Contact removed from list");
      await Promise.all([reloadMembers(), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to remove contact");
    } finally {
      setBusyRow(null);
    }
  };

  const handleDeleteOneContact = async (contact: Contact) => {
    if (!viewListId) return;
    if (!window.confirm(`Permanently delete ${displayName(contact)} (${contact.phone_number})?\n\nThis deletes the contact, its messages and all related history. It cannot be undone.`)) return;
    try {
      setBusyRow(`delete-${contact.id}`);
      const { data } = await api.post(`/lists/${viewListId}/contacts/delete`, { contact_ids: [contact.id] });
      toast.success(`${data.deleted ?? 1} phone number permanently deleted`);
      await Promise.all([reloadMembers(), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete contact");
    } finally {
      setBusyRow(null);
    }
  };

  const handleDeleteListWithContacts = async () => {
    if (!viewListId) return;
    if (!window.confirm(`Delete "${viewListName}" AND permanently delete all ${listTotal} phone number${listTotal === 1 ? "" : "s"} in it?\n\nThis cannot be undone.`)) return;
    try {
      setDeletingList(true);
      await api.delete(`/lists/${viewListId}`, { params: { delete_contacts: true } });
      toast.success("List and its numbers permanently deleted");
      closeListEditor();
      loadLists();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to delete list");
    } finally {
      setDeletingList(false);
    }
  };

  // ---- Add contacts: search-as-you-type against the server ----------------

  const openAddContacts = () => {
    setShowAddContacts(true);
    setSelectedToAdd(new Set());
    setAddPage(1);
    setAddQuery("");
    loadAddPage(1, "");
  };

  const loadAddPage = async (page: number, query: string, quiet = false) => {
    if (!viewListId) return;
    if (!quiet) setAddLoading(true);
    try {
      const { data } = await api.get<PaginatedResponse<Contact>>("/contacts/", {
        params: {
          page,
          per_page: PAGE_SIZE,
          search: query.trim() || undefined,
          exclude_list_id: viewListId,
        },
      });
      setAddItems(data.items);
      setAddTotal(data.total);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to load contacts");
      setAddItems([]);
      setAddTotal(0);
    } finally {
      if (!quiet) setAddLoading(false);
    }
  };

  const changeAddQuery = (q: string) => {
    setAddQuery(q);
    if (addTimer.current) window.clearTimeout(addTimer.current);
    addTimer.current = window.setTimeout(() => {
      setAddPage(1);
      loadAddPage(1, q);
    }, 300);
  };

  const toggleToAdd = (id: number) => {
    const next = new Set(selectedToAdd);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelectedToAdd(next);
  };

  const handleAddContacts = async () => {
    if (!viewListId || selectedToAdd.size === 0) return;
    const count = selectedToAdd.size;
    try {
      setAddingToList(true);
      const { data } = await api.post(`/lists/${viewListId}/contacts`, [...selectedToAdd]);
      toast.success(`${data.added ?? count} contact${(data.added ?? count) === 1 ? "" : "s"} added`);
      setSelectedToAdd(new Set());
      setShowAddContacts(false);
      setMemberPage(1);
      await Promise.all([loadListContacts(viewListId, 1, memberSearch, true), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to add contacts");
    } finally {
      setAddingToList(false);
    }
  };

  const addPages = Math.max(1, Math.ceil(addTotal / PAGE_SIZE));
  const memberPages = Math.max(1, Math.ceil(listMatchingTotal / PAGE_SIZE));
  const memberStart = listMatchingTotal === 0 ? 0 : (memberPage - 1) * PAGE_SIZE + 1;
  const memberEnd = Math.min(memberPage * PAGE_SIZE, listMatchingTotal);

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadLists} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-20 lg:pb-0">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold">Contact Lists</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Create a list, then add or remove contacts at any time — even lists with thousands of
            members stay fast (rows are paged, actions run on the server).
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> Create List
        </button>
      </div>

      {showCreate && (
        <div className="card p-4 flex flex-col sm:flex-row gap-2">
          <input
            className="input flex-1"
            placeholder="List name..."
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && handleCreate()}
            autoFocus
          />
          <button onClick={handleCreate} className="btn-primary">Create</button>
          <button onClick={() => { setShowCreate(false); setNewName(""); }} className="btn-secondary">Cancel</button>
        </div>
      )}

      {viewListId !== null && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-0 sm:p-4">
          <div className="bg-white dark:bg-[#202c33] w-full sm:max-w-2xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl">
            <div className="sticky top-0 z-10 bg-[#008069] dark:bg-[#202c33] px-4 py-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-white font-semibold truncate">Edit {viewListName}</h2>
                <p className="text-white/70 text-xs">{listTotal} contact{listTotal === 1 ? "" : "s"} in this list</p>
              </div>
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={openAddContacts}
                  disabled={listLoading}
                  className="px-3 py-2 rounded-full bg-white text-[#008069] text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
                >
                  <UserPlus size={14} /> Add Contacts
                </button>
                <button onClick={closeListEditor} className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-white"><X size={16} /></button>
              </div>
            </div>

            <div className="p-4 sm:p-6 space-y-4">
              <div className="rounded-xl border border-red-200 bg-red-50/60 dark:border-red-900/40 dark:bg-red-950/20 p-3 flex flex-col sm:flex-row items-center gap-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-red-700 dark:text-red-300">Delete this list</p>
                  <p className="text-xs text-red-600/80 dark:text-red-400/80">
                    Delete the list only, or also permanently delete every phone number in it.
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 flex-shrink-0">
                  <button
                    onClick={() => handleDelete(viewListId)}
                    disabled={deletingList}
                    className="px-3 py-1.5 rounded-full text-xs font-semibold bg-white dark:bg-[#2a3942] text-red-600 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-900/30 disabled:opacity-50"
                  >
                    Delete list only
                  </button>
                  <button
                    onClick={handleDeleteListWithContacts}
                    disabled={deletingList}
                    className="px-3 py-1.5 rounded-full text-xs font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-50"
                  >
                    {deletingList ? "Deleting…" : "Delete list + all numbers"}
                  </button>
                </div>
              </div>

              {showAddContacts && (
                <div className="rounded-xl border border-gray-200 dark:border-[#2a3942] bg-gray-50 dark:bg-[#111b21] p-3 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold text-sm">Add contacts to {viewListName}</h3>
                    <button onClick={() => setShowAddContacts(false)} className="w-7 h-7 rounded-full bg-gray-200 dark:bg-[#2a3942] flex items-center justify-center text-gray-600 dark:text-gray-300"><X size={14} /></button>
                  </div>
                  <p className="text-xs text-gray-500">
                    Search your contacts — results load as you type, so this stays fast even with
                    thousands of contacts. Numbers already in this list are hidden automatically.
                  </p>
                  <div className="relative">
                    <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                    <input
                      value={addQuery}
                      onChange={(e) => changeAddQuery(e.target.value)}
                      placeholder="Search name, business or phone…"
                      className="w-full pl-9 pr-3 py-2.5 bg-white dark:bg-[#2a3942] rounded-xl text-sm border border-gray-200 dark:border-[#2a3942] focus:outline-none focus:ring-2 focus:ring-[#00a884]/30"
                      autoFocus
                    />
                  </div>
                  <div className="border dark:border-[#2a3942] rounded-xl overflow-hidden bg-white dark:bg-[#202c33] max-h-72 overflow-y-auto">
                    {addLoading ? (
                      <div className="py-8 text-center text-sm text-gray-500">Loading…</div>
                    ) : addItems.length === 0 ? (
                      <div className="py-8 text-center text-gray-500 text-sm">
                        {addQuery.trim()
                          ? "No contacts match — try a different search."
                          : "No contacts left to add — every contact is already in this list."}
                      </div>
                    ) : (
                      <div className="divide-y divide-gray-100 dark:divide-[#2a3942]">
                        {addItems.map((contact) => (
                          <label
                            key={contact.id}
                            className="flex items-center gap-3 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-[#111b21] cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              checked={selectedToAdd.has(contact.id)}
                              onChange={() => toggleToAdd(contact.id)}
                              className="rounded accent-[#00a884] flex-shrink-0"
                            />
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium truncate">{displayName(contact)}</p>
                              <p className="text-xs text-gray-500 truncate">{contact.phone_number}</p>
                            </div>
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                  {addTotal > PAGE_SIZE && (
                    <div className="flex items-center justify-between text-xs text-gray-500">
                      <span>{addTotal} available{addQuery.trim() ? ` · “${addQuery.trim()}”` : ""}</span>
                      <div className="flex gap-1">
                        <button
                          onClick={() => { const p = Math.max(1, addPage - 1); setAddPage(p); loadAddPage(p, addQuery, true); }}
                          disabled={addPage <= 1 || addLoading}
                          className="px-2 py-1 rounded-md bg-white dark:bg-[#2a3942] border disabled:opacity-40"
                        >
                          <ChevronLeft size={14} />
                        </button>
                        <span className="px-2 py-1">Page {addPage} / {addPages}</span>
                        <button
                          onClick={() => { const p = Math.min(addPages, addPage + 1); setAddPage(p); loadAddPage(p, addQuery, true); }}
                          disabled={addPage >= addPages || addLoading}
                          className="px-2 py-1 rounded-md bg-white dark:bg-[#2a3942] border disabled:opacity-40"
                        >
                          <ChevronRight size={14} />
                        </button>
                      </div>
                    </div>
                  )}
                  {selectedToAdd.size > 0 && (
                    <button
                      onClick={handleAddContacts}
                      disabled={addingToList || addLoading}
                      className="w-full py-2.5 rounded-full bg-[#00a884] hover:bg-[#06cf9c] text-white text-sm font-semibold disabled:opacity-50"
                    >
                      {addingToList ? "Adding…" : `Add ${selectedToAdd.size} selected contact${selectedToAdd.size === 1 ? "" : "s"}`}
                    </button>
                  )}
                </div>
              )}

              <div>
                <div className="flex items-center justify-between gap-3 mb-2 flex-wrap">
                  <h3 className="font-semibold text-sm">Contacts in this list</h3>
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="relative">
                      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                      <input
                        value={memberSearchInput}
                        onChange={(e) => { setMemberSearchInput(e.target.value); applyMemberSearch(e.target.value); }}
                        placeholder="Search this list…"
                        className="pl-8 pr-2 py-1.5 rounded-full text-xs bg-gray-100 dark:bg-[#2a3942] border border-transparent focus:outline-none focus:ring-2 focus:ring-[#00a884]/30 w-44"
                      />
                    </div>
                    {listContacts.length > 0 && !listLoading && (
                      <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={allMatchingInList || pageAllSelected}
                          onChange={toggleAllInList}
                          className="rounded accent-[#00a884]"
                        />
                        {allMatchingInList
                          ? "All matching selected"
                          : pageAllSelected && listMatchingTotal > listContacts.length
                            ? `Select all ${listMatchingTotal} matching`
                            : "Select this page"}
                      </label>
                    )}
                  </div>
                </div>

                {selectionCount > 0 && (
                  <div className="mb-2 rounded-xl border border-[#00a884]/30 bg-[#f0f9f6] dark:bg-[#0a332c]/20 p-2.5 flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-[#008069] dark:text-[#00a884] flex-1 min-w-[80px]">
                      {selectedLabel}
                    </span>
                    <button
                      onClick={handleBulkRemoveFromList}
                      disabled={removingBulk || deletingPermanently}
                      className="px-3 py-1.5 rounded-full text-xs font-semibold bg-white dark:bg-[#2a3942] text-[#54656f] dark:text-[#aebac1] hover:bg-gray-100 disabled:opacity-50 flex items-center gap-1"
                    >
                      {removingBulk ? "Removing…" : <><Minus size={12} /> Remove from list</>}
                    </button>
                    <button
                      onClick={handlePermanentDeleteInList}
                      disabled={removingBulk || deletingPermanently}
                      className="px-3 py-1.5 rounded-full text-xs font-semibold text-white bg-red-600 hover:bg-red-500 disabled:opacity-50 flex items-center gap-1"
                    >
                      {deletingPermanently ? "Deleting…" : <><Trash2 size={12} /> Delete permanently</>}
                    </button>
                    <button
                      onClick={clearSelection}
                      className="px-3 py-1.5 rounded-full text-xs font-semibold text-gray-500 hover:bg-gray-100 dark:hover:bg-[#2a3942]"
                    >
                      Clear
                    </button>
                  </div>
                )}

                <div className="border dark:border-[#2a3942] rounded-xl overflow-hidden">
                  {listLoading ? (
                    <div className="py-10 text-center text-sm text-gray-500">
                      <div className="w-7 h-7 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                      Loading list…
                    </div>
                  ) : listContacts.length === 0 ? (
                    <div className="py-10 text-center text-gray-500">
                      <Users size={32} className="mx-auto mb-2 opacity-30" />
                      <p className="text-sm">
                        {memberSearch.trim()
                          ? `No contacts match “${memberSearch.trim()}” in this list.`
                          : "No contacts in this list"}
                      </p>
                      <button onClick={openAddContacts} className="btn-primary btn-sm mt-3"><UserPlus size={14} className="mr-1" /> Add contacts</button>
                    </div>
                  ) : (
                    <div className="divide-y dark:divide-[#2a3942]">
                      {listContacts.map((contact) => {
                        const isSelected = allMatchingInList || selectedInList.has(contact.id);
                        const removing = busyRow === `remove-${contact.id}`;
                        const deleting = busyRow === `delete-${contact.id}`;
                        return (
                          <div
                            key={contact.id}
                            className={`flex items-center gap-3 p-3 hover:bg-gray-50 dark:hover:bg-[#111b21] ${isSelected ? "bg-[#f0f9f6] dark:bg-[#0a332c]/20" : ""}`}
                          >
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleInList(contact.id)}
                              className="rounded accent-[#00a884] flex-shrink-0"
                              title="Select this phone number"
                            />
                            <div className="min-w-0 flex-1">
                              <p className="font-medium text-sm truncate">{displayName(contact)}</p>
                              <p className="text-xs text-gray-500 truncate">
                                {contact.phone_number}{contact.business_name && displayName(contact) !== contact.business_name ? ` · ${contact.business_name}` : ""}
                              </p>
                            </div>
                            <div className="flex items-center gap-1.5 flex-shrink-0">
                              <button
                                onClick={() => handleDeleteOneContact(contact)}
                                disabled={removing || deleting || removingBulk || deletingPermanently}
                                className="w-8 h-8 rounded-full text-red-600 bg-red-50 hover:bg-red-100 dark:bg-red-900/20 flex items-center justify-center disabled:opacity-40"
                                title="Permanently delete this phone number (fast)"
                              >
                                {deleting ? <span className="w-3 h-3 border-2 border-red-500 border-t-transparent rounded-full animate-spin" /> : <Trash2 size={13} />}
                              </button>
                              <button
                                onClick={() => handleRemoveContact(contact)}
                                disabled={removing || deleting || removingBulk || deletingPermanently}
                                className="px-2.5 py-1.5 rounded-full text-xs font-semibold text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-[#2a3942] hover:bg-gray-200 dark:hover:bg-[#3a4a54] flex items-center gap-1 disabled:opacity-40"
                                title="Remove from this list (keep the number)"
                              >
                                {removing ? "Removing…" : "Remove"}
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {listMatchingTotal > PAGE_SIZE && (
                  <div className="flex items-center justify-between mt-2 text-xs text-gray-500 flex-wrap gap-2">
                    <span>
                      Showing {memberStart}–{memberEnd} of {listMatchingTotal}
                      {memberSearch.trim() ? ` matching “${memberSearch.trim()}”` : ""}
                    </span>
                    <div className="flex gap-1 items-center">
                      <button
                        onClick={() => { const p = Math.max(1, memberPage - 1); setMemberPage(p); setSelectedInList(new Set()); setAllMatchingInList(false); if (viewListId) loadListContacts(viewListId, p, memberSearch, true); }}
                        disabled={memberPage <= 1 || listLoading}
                        className="px-2.5 py-1 rounded-lg border bg-white dark:bg-[#2a3942] disabled:opacity-40 flex items-center gap-1"
                      >
                        <ChevronLeft size={14} /> Prev
                      </button>
                      <span className="px-2">Page {memberPage} / {memberPages}</span>
                      <button
                        onClick={() => { const p = Math.min(memberPages, memberPage + 1); setMemberPage(p); setSelectedInList(new Set()); setAllMatchingInList(false); if (viewListId) loadListContacts(viewListId, p, memberSearch, true); }}
                        disabled={memberPage >= memberPages || listLoading}
                        className="px-2.5 py-1 rounded-lg border bg-white dark:bg-[#2a3942] disabled:opacity-40 flex items-center gap-1"
                      >
                        Next <ChevronRight size={14} />
                      </button>
                    </div>
                  </div>
                )}
                {!listLoading && listMatchingTotal > 0 && (
                  <p className="text-[11px] text-gray-400 mt-1.5">
                    Tip: tick rows (or “Select all {listMatchingTotal} matching”) then Remove / Delete — bulk actions run on the server, so even a 50,000-contact list is instant.
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          [...Array(6)].map((_, index) => (
            <div key={index} className="card p-4"><div className="skeleton h-5 w-32 mb-2" /><div className="skeleton h-4 w-20" /></div>
          ))
        ) : lists.length === 0 ? (
          <div className="col-span-full text-center py-12 text-gray-500">
            <Users size={48} className="mx-auto mb-2 opacity-30" />
            <p>No lists yet. Create your first contact list.</p>
          </div>
        ) : (
          lists.map((list) => (
            <div key={list.id} className="card p-4 hover:shadow-md transition-shadow">
              {editingId === list.id ? (
                <div className="space-y-2">
                  <input
                    className="input w-full text-sm"
                    value={editName}
                    onChange={(event) => setEditName(event.target.value)}
                    onKeyDown={(event) => event.key === "Enter" && handleRename(list.id)}
                    autoFocus
                  />
                  <div className="flex gap-2">
                    <button onClick={() => handleRename(list.id)} className="btn-primary btn-sm">Save</button>
                    <button onClick={() => setEditingId(null)} className="btn-secondary btn-sm">Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="font-semibold truncate">{list.name}</h3>
                      {list.description && <p className="text-sm text-gray-500 mt-1 line-clamp-2">{list.description}</p>}
                    </div>
                    <button onClick={() => handleDelete(list.id)} className="btn-ghost btn-sm text-red-600 flex-shrink-0" title="Delete list"><Trash2 size={14} /></button>
                  </div>
                  <div className="flex items-center gap-2 mt-3 text-sm text-gray-500">
                    <Users size={14} />
                    <span>{list.contact_count} contact{list.contact_count === 1 ? "" : "s"}</span>
                  </div>
                  <div className="flex gap-2 mt-4 pt-3 border-t dark:border-gray-700">
                    <button onClick={() => openListEditor(list)} className="btn-primary btn-sm flex-1">
                      <Edit2 size={14} className="mr-1.5" /> Edit contacts
                    </button>
                    <button onClick={() => { setEditingId(list.id); setEditName(list.name); }} className="btn-secondary btn-sm" title="Rename list">Rename</button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
