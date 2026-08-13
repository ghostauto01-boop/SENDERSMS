import { useEffect, useMemo, useState } from "react";
import api from "../api/client";
import { Contact, PaginatedResponse } from "../types";
import toast from "react-hot-toast";
import { Edit2, Plus, Search, Trash2, UserPlus, Users, X } from "lucide-react";

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

async function fetchAllContactPages(path: string): Promise<{ items: Contact[]; total: number }> {
  // The contacts API deliberately caps each page at 100. This page used to
  // request 200, which FastAPI rejected with 422 and made Add Contacts look as
  // if there were zero contacts. Fetch valid 100-contact pages instead.
  const first = await api.get<PaginatedResponse<Contact>>(path, {
    params: { page: 1, per_page: 100 },
  });
  const pages = Math.ceil(first.data.total / 100);
  if (pages <= 1) return first.data;
  const rest = await Promise.all(
    Array.from({ length: pages - 1 }, (_, index) =>
      api.get<PaginatedResponse<Contact>>(path, {
        params: { page: index + 2, per_page: 100 },
      })
    )
  );
  return {
    total: first.data.total,
    items: [first.data.items, ...rest.map((response) => response.data.items)].flat(),
  };
}

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
  const [listContacts, setListContacts] = useState<Contact[]>([]);
  const [listTotal, setListTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [showAddContacts, setShowAddContacts] = useState(false);
  const [allContacts, setAllContacts] = useState<Contact[]>([]);
  const [allContactsTotal, setAllContactsTotal] = useState(0);
  const [addContactsLoading, setAddContactsLoading] = useState(false);
  const [selectedToAdd, setSelectedToAdd] = useState<Set<number>>(new Set());
  const [addSearch, setAddSearch] = useState("");
  const [removingId, setRemovingId] = useState<number | null>(null);

  useEffect(() => { loadLists(); }, []);

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

  const loadListContacts = async (listId: number) => {
    try {
      setListLoading(true);
      const data = await fetchAllContactPages(`/lists/${listId}/contacts`);
      setListContacts(data.items);
      setListTotal(data.total);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to load list contacts");
    } finally {
      setListLoading(false);
    }
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

  const openListEditor = async (list: ListItem) => {
    setViewListId(list.id);
    setViewListName(list.name);
    setListContacts([]);
    setListTotal(list.contact_count);
    setShowAddContacts(false);
    setSelectedToAdd(new Set());
    setAddSearch("");
    await loadListContacts(list.id);
  };

  const closeListEditor = () => {
    setViewListId(null);
    setShowAddContacts(false);
    setSelectedToAdd(new Set());
    setAddSearch("");
  };

  const handleAddContacts = async () => {
    if (!viewListId || selectedToAdd.size === 0) return;
    const count = selectedToAdd.size;
    try {
      const { data } = await api.post(`/lists/${viewListId}/contacts`, [...selectedToAdd]);
      toast.success(`${data.added ?? count} contact${(data.added ?? count) === 1 ? "" : "s"} added`);
      setSelectedToAdd(new Set());
      setShowAddContacts(false);
      await Promise.all([loadListContacts(viewListId), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to add contacts");
    }
  };

  const handleRemoveContact = async (contact: Contact) => {
    if (!viewListId) return;
    if (!window.confirm(`Remove ${displayName(contact)} from ${viewListName}? The contact will not be deleted.`)) return;
    try {
      setRemovingId(contact.id);
      await api.post(`/lists/${viewListId}/contacts/remove`, { contact_ids: [contact.id] });
      toast.success("Contact removed from list");
      await Promise.all([loadListContacts(viewListId), loadLists()]);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to remove contact");
    } finally {
      setRemovingId(null);
    }
  };

  const openAddContacts = async () => {
    setShowAddContacts(true);
    setSelectedToAdd(new Set());
    setAddSearch("");
    try {
      setAddContactsLoading(true);
      const data = await fetchAllContactPages("/contacts/");
      setAllContacts(data.items);
      setAllContactsTotal(data.total);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to load contacts");
      setAllContacts([]);
      setAllContactsTotal(0);
    } finally {
      setAddContactsLoading(false);
    }
  };

  const availableContacts = useMemo(() => {
    const members = new Set(listContacts.map((contact) => contact.id));
    return allContacts.filter((contact) => !members.has(contact.id));
  }, [allContacts, listContacts]);

  const filteredAvailableContacts = useMemo(() => {
    const needle = addSearch.trim().toLowerCase();
    if (!needle) return availableContacts;
    return availableContacts.filter((contact) =>
      [
        contact.first_name,
        contact.last_name,
        contact.business_name,
        contact.phone_number,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    );
  }, [availableContacts, addSearch]);

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
          <p className="text-sm text-gray-500 mt-0.5">Create a list, then add or remove contacts at any time.</p>
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
              {showAddContacts && (
                <div className="border border-[#00a884]/30 bg-[#f0f9f6] dark:bg-[#0a332c]/20 rounded-xl p-3 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <h3 className="font-semibold text-sm">Add contacts</h3>
                      {!addContactsLoading && (
                        <p className="text-xs text-gray-500">
                          {availableContacts.length} available · {allContactsTotal} total contacts
                        </p>
                      )}
                    </div>
                    <button onClick={() => { setShowAddContacts(false); setSelectedToAdd(new Set()); }} className="btn-ghost btn-sm"><X size={15} /></button>
                  </div>

                  <div className="relative">
                    <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                    <input
                      className="input pl-9 py-2 text-sm"
                      placeholder="Search name, business or phone..."
                      value={addSearch}
                      onChange={(event) => setAddSearch(event.target.value)}
                    />
                  </div>

                  <div className="max-h-56 overflow-y-auto space-y-1 bg-white dark:bg-[#111b21] rounded-lg p-1">
                    {addContactsLoading ? (
                      <div className="py-8 text-center text-sm text-gray-500">
                        <div className="w-6 h-6 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                        Loading contacts…
                      </div>
                    ) : allContactsTotal === 0 ? (
                      <p className="py-8 text-center text-sm text-gray-500">No contacts exist yet. Create a contact first.</p>
                    ) : availableContacts.length === 0 ? (
                      <p className="py-8 text-center text-sm text-gray-500">All contacts are already in this list.</p>
                    ) : filteredAvailableContacts.length === 0 ? (
                      <p className="py-8 text-center text-sm text-gray-500">No contacts match your search.</p>
                    ) : (
                      filteredAvailableContacts.map((contact) => (
                        <label
                          key={contact.id}
                          className={`flex items-center gap-3 p-2.5 rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-[#202c33] ${selectedToAdd.has(contact.id) ? "bg-primary-50 dark:bg-primary-900/20" : ""}`}
                        >
                          <input
                            type="checkbox"
                            checked={selectedToAdd.has(contact.id)}
                            onChange={() => {
                              const next = new Set(selectedToAdd);
                              next.has(contact.id) ? next.delete(contact.id) : next.add(contact.id);
                              setSelectedToAdd(next);
                            }}
                            className="rounded"
                          />
                          <div className="min-w-0">
                            <p className="text-sm font-medium truncate">{displayName(contact)}</p>
                            <p className="text-xs text-gray-500">{contact.phone_number}</p>
                          </div>
                        </label>
                      ))
                    )}
                  </div>

                  {availableContacts.length > 0 && !addContactsLoading && (
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs text-gray-500">
                        {selectedToAdd.size > 0 ? `${selectedToAdd.size} selected` : "Select one or more contacts"}
                      </span>
                      <button onClick={handleAddContacts} disabled={selectedToAdd.size === 0} className="btn-primary btn-sm">
                        Add selected
                      </button>
                    </div>
                  )}
                </div>
              )}

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold text-sm">Contacts in this list</h3>
                  <span className="text-xs text-gray-500">Removing here does not delete the contact</span>
                </div>
                <div className="border dark:border-[#2a3942] rounded-xl overflow-hidden">
                  {listLoading ? (
                    <div className="py-10 text-center text-sm text-gray-500">
                      <div className="w-7 h-7 border-2 border-[#00a884] border-t-transparent rounded-full animate-spin mx-auto mb-2" />
                      Loading list…
                    </div>
                  ) : listContacts.length === 0 ? (
                    <div className="py-10 text-center text-gray-500">
                      <Users size={32} className="mx-auto mb-2 opacity-30" />
                      <p className="text-sm">No contacts in this list</p>
                      <button onClick={openAddContacts} className="btn-primary btn-sm mt-3"><UserPlus size={14} className="mr-1" /> Add contacts</button>
                    </div>
                  ) : (
                    <div className="divide-y dark:divide-[#2a3942]">
                      {listContacts.map((contact) => (
                        <div key={contact.id} className="flex items-center justify-between gap-3 p-3 hover:bg-gray-50 dark:hover:bg-[#111b21]">
                          <div className="min-w-0">
                            <p className="font-medium text-sm truncate">{displayName(contact)}</p>
                            <p className="text-xs text-gray-500 truncate">
                              {contact.phone_number}{contact.business_name && displayName(contact) !== contact.business_name ? ` · ${contact.business_name}` : ""}
                            </p>
                          </div>
                          <button
                            onClick={() => handleRemoveContact(contact)}
                            disabled={removingId === contact.id}
                            className="px-3 py-1.5 rounded-full text-xs font-semibold text-red-600 bg-red-50 hover:bg-red-100 dark:bg-red-900/20 flex items-center gap-1.5 disabled:opacity-50 flex-shrink-0"
                            title="Remove from this list"
                          >
                            <Trash2 size={13} /> {removingId === contact.id ? "Removing…" : "Remove"}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
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
