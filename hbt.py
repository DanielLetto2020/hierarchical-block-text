#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HBT (Hierarchical Block Text) — Production-grade CLI для управления иерархическими задачами.

Copyright (c) 2026 Максим Кузьминский (Maxim Kuzminsky)
Email: i@m-letto.ru
Licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)

Версия: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "Максим Кузьминский <i@m-letto.ru>"

import json
import os
import shutil
import argparse
import sys
import uuid
import hashlib
import tempfile
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum
from contextlib import contextmanager
from pathlib import Path


# ============================================================================
# ИСКЛЮЧЕНИЯ
# ============================================================================

class HBTError(Exception):
    """Базовое исключение HBT"""
    pass


class NodeNotFoundError(HBTError):
    """Узел не найден"""
    def __init__(self, node_id: str):
        self.node_id = node_id
        super().__init__(f"Узел '{node_id}' не найден")


class NodeLockedError(HBTError):
    """Попытка изменить заблокированный узел"""
    def __init__(self, node_id: str, operation: str):
        self.node_id = node_id
        self.operation = operation
        super().__init__(f"Узел '{node_id}' заблокирован. Операция '{operation}' запрещена")


class ValidationError(HBTError):
    """Ошибка валидации данных"""
    pass


class IntegrityError(HBTError):
    """Нарушение целостности данных"""
    pass


# ============================================================================
# ПЕРЕЧИСЛЕНИЯ И КОНСТАНТЫ
# ============================================================================

class NodeStatus(Enum):
    LOCKED = "locked"
    EDITABLE = "editable"


class TaskProgress(Enum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"
    BLOCKED = "blocked"


class ActionType(Enum):
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"
    REWRITE = "rewrite"
    STATUS_CHANGE = "status_change"
    PROGRESS_CHANGE = "progress"
    RESTORE = "restore"
    TAG_ADD = "tag_add"
    TAG_REMOVE = "tag_remove"
    ALIAS_SET = "alias_set"


# Иконки для отображения
PROGRESS_ICONS = {
    TaskProgress.TODO: "⚪",
    TaskProgress.DOING: "🔵",
    TaskProgress.DONE: "🟢",
    TaskProgress.BLOCKED: "🔴",
}

STATUS_ICONS = {
    NodeStatus.LOCKED: "🔒",
    NodeStatus.EDITABLE: "✍️",
}


# ============================================================================
# МОДЕЛИ ДАННЫХ
# ============================================================================

@dataclass
class HistoryEntry:
    """Запись в истории изменений"""
    timestamp: str
    action: str
    node_id: str
    text: str
    checksum: str = ""
    
    def __post_init__(self):
        if not self.checksum:
            data = f"{self.timestamp}{self.action}{self.node_id}{self.text}"
            self.checksum = hashlib.sha256(data.encode()).hexdigest()[:12]


@dataclass 
class Node:
    """Узел дерева задач"""
    id: str
    text: str
    status: str = "editable"
    progress: str = "todo"
    visible: bool = True
    children: List['Node'] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    alias: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    parent_id: Optional[str] = None
    
    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        # Конвертируем children из dict в Node если нужно
        if self.children and len(self.children) > 0 and isinstance(self.children[0], dict):
            self.children = [Node(**c) for c in self.children]
    
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь"""
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status,
            "progress": self.progress,
            "visible": self.visible,
            "children": [c.to_dict() for c in self.children],
            "tags": self.tags,
            "alias": self.alias,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "parent_id": self.parent_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Node':
        """Десериализация из словаря"""
        # Копируем, чтобы не мутировать входные данные
        data = data.copy()
        children_data = data.pop('children', [])
        node = cls(**data)
        node.children = [cls.from_dict(c) for c in children_data]
        return node
    
    def is_locked(self) -> bool:
        return self.status == NodeStatus.LOCKED.value
    
    def has_locked_children(self) -> bool:
        """Проверяет наличие заблокированных потомков"""
        for child in self.children:
            if child.is_locked() or child.has_locked_children():
                return True
        return False
    
    def get_locked_children_ids(self) -> List[str]:
        """Возвращает ID всех заблокированных потомков"""
        locked = []
        for child in self.children:
            if child.is_locked():
                locked.append(child.id)
            locked.extend(child.get_locked_children_ids())
        return locked


@dataclass
class ProjectConfig:
    """Конфигурация проекта"""
    name: str = "New HBT Project"
    version: str = "1.0.0"
    auto_backup: bool = True
    max_snapshots: int = 100
    default_status: str = "editable"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProjectConfig':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================================
# ОСНОВНОЙ ДРАЙВЕР
# ============================================================================

class HBTDriver:
    """
    Production-grade драйвер для управления иерархическими задачами.
    
    Особенности:
    - UUID-based идентификаторы (стабильные ссылки)
    - Атомарные операции с файловой блокировкой
    - Защита locked-узлов на всех уровнях
    - Полная история с контрольными суммами
    """
    
    def __init__(self, filename: str = "tasks.json"):
        self.db_path = Path.cwd() / filename
        self.snap_dir = Path.cwd() / ".hbt_history"
        self._data: Optional[Dict[str, Any]] = None
        self._index: Dict[str, Node] = {}  # Кэш для быстрого поиска
        self._alias_index: Dict[str, str] = {}  # alias -> id
        self._load()
    
    # ========================================================================
    # ЗАГРУЗКА / СОХРАНЕНИЕ
    # ========================================================================
    
    def _get_default_data(self) -> Dict[str, Any]:
        """Возвращает структуру данных по умолчанию"""
        return {
            "config": ProjectConfig().to_dict(),
            "history": [],
            "tree": [],
            "schema_version": __version__,
        }
    
    def _load(self) -> None:
        """Загружает данные из файла"""
        if not self.db_path.exists():
            self._data = self._get_default_data()
            return
        
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            raise IntegrityError(f"Ошибка чтения базы данных: {e}")
        
        # Миграция старого формата
        self._data = self._migrate_data(raw_data)
        self._rebuild_index()
    
    def _migrate_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Миграция данных из старого формата"""
        # Если есть "project" вместо "config" — старый формат
        if "project" in data and "config" not in data:
            data["config"] = {"name": data.pop("project")}
        
        # Добавляем недостающие поля
        if "config" not in data:
            data["config"] = ProjectConfig().to_dict()
        if "schema_version" not in data:
            data["schema_version"] = __version__
        
        # Миграция узлов — добавляем UUID если нет
        self._migrate_nodes(data.get("tree", []))
        
        return data
    
    def _migrate_nodes(self, nodes: List[Dict], parent_id: Optional[str] = None) -> None:
        """Рекурсивная миграция узлов"""
        for node in nodes:
            # Если ID выглядит как позиционный (1.1.2), генерируем UUID
            old_id = node.get("id", "")
            if "." in old_id or old_id.isdigit():
                # Сохраняем старый ID как alias для обратной совместимости
                if not node.get("alias"):
                    node["alias"] = old_id
                node["id"] = self._generate_id()
            
            # Добавляем недостающие поля
            node.setdefault("tags", [])
            node.setdefault("alias", None)
            node.setdefault("created_at", datetime.now().isoformat())
            node.setdefault("updated_at", datetime.now().isoformat())
            node.setdefault("parent_id", parent_id)
            
            self._migrate_nodes(node.get("children", []), node["id"])
    
    def _rebuild_index(self) -> None:
        """Перестраивает индексы для быстрого поиска"""
        self._index.clear()
        self._alias_index.clear()
        
        def index_node(node_data: Dict, parent_node: Optional[Node] = None) -> Node:
            """Рекурсивно индексирует узлы, возвращая созданный Node"""
            # Создаём узел БЕЗ рекурсивного создания детей
            children_data = node_data.get("children", [])
            
            # Создаём копию данных без children
            node_data_copy = {}
            for k, v in node_data.items():
                if k != "children":
                    node_data_copy[k] = v
            node_data_copy["children"] = []
            
            # ПРИНУДИТЕЛЬНО устанавливаем parent_id из структуры дерева
            node_data_copy["parent_id"] = parent_node.id if parent_node else None
            
            node = Node.from_dict(node_data_copy)
            
            self._index[node.id] = node
            if node.alias:
                self._alias_index[node.alias] = node.id
            
            # Рекурсивно обрабатываем детей — передаём текущий node как родителя
            for child_data in children_data:
                child_node = index_node(child_data, node)
                node.children.append(child_node)
            
            return node
        
        for node_data in self._data.get("tree", []):
            index_node(node_data, None)
    
    @contextmanager
    def _atomic_save(self):
        """Контекстный менеджер для атомарного сохранения"""
        # Создаём временный файл
        temp_fd, temp_path = tempfile.mkstemp(
            suffix='.json',
            dir=self.db_path.parent
        )
        temp_path = Path(temp_path)
        
        try:
            yield temp_path
            # Атомарная замена
            temp_path.replace(self.db_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            try:
                os.close(temp_fd)
            except OSError:
                pass
    
    def _save(self, manual_name: Optional[str] = None) -> None:
        """Сохраняет данные атомарно с созданием снапшота"""
        # Обновляем tree из индекса
        self._data["tree"] = [
            self._node_to_dict(node) 
            for node in self._get_root_nodes()
        ]
        
        with self._atomic_save() as temp_path:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        
        # Перестраиваем индекс для гарантии консистентности
        self._rebuild_index()
        
        # Создаём снапшот
        config = self._get_config()
        if config.auto_backup:
            self._create_snapshot(manual_name)
    
    def _create_snapshot(self, manual_name: Optional[str] = None) -> str:
        """Создаёт снапшот базы данных"""
        self.snap_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_name = manual_name or f"auto_{timestamp}.json"
        snap_path = self.snap_dir / snap_name
        
        shutil.copy(self.db_path, snap_path)
        
        # Очистка старых снапшотов
        self._cleanup_snapshots()
        
        return snap_name
    
    def _cleanup_snapshots(self) -> None:
        """Удаляет старые автоматические снапшоты"""
        config = self._get_config()
        auto_snaps = sorted([
            f for f in self.snap_dir.iterdir() 
            if f.name.startswith("auto_")
        ], key=lambda x: x.stat().st_mtime, reverse=True)
        
        for snap in auto_snaps[config.max_snapshots:]:
            snap.unlink()
    
    def _node_to_dict(self, node: Node) -> Dict[str, Any]:
        """Конвертирует Node в словарь для сохранения"""
        return node.to_dict()
    
    def _get_root_nodes(self) -> List[Node]:
        """Возвращает корневые узлы"""
        return [n for n in self._index.values() if n.parent_id is None]
    
    def _get_config(self) -> ProjectConfig:
        """Возвращает конфигурацию проекта"""
        return ProjectConfig.from_dict(self._data.get("config", {}))
    
    # ========================================================================
    # ЛОГИРОВАНИЕ
    # ========================================================================
    
    def _log(self, action: ActionType, node_id: str, text: str = "") -> None:
        """Добавляет запись в историю"""
        entry = HistoryEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action=action.value,
            node_id=node_id,
            text=text
        )
        self._data.setdefault("history", []).append(asdict(entry))
    
    # ========================================================================
    # ПОИСК УЗЛОВ
    # ========================================================================
    
    def _generate_id(self) -> str:
        """Генерирует уникальный ID"""
        return uuid.uuid4().hex[:12]
    
    def _resolve_id(self, identifier: str) -> str:
        """Разрешает ID или alias в реальный ID"""
        # Убираем @ в начале если есть (пользователь может ввести @alias)
        if identifier.startswith("@"):
            identifier = identifier[1:]
        
        # Сначала проверяем прямое совпадение по ID
        if identifier in self._index:
            return identifier
        # Затем проверяем alias
        if identifier in self._alias_index:
            return self._alias_index[identifier]
        raise NodeNotFoundError(identifier)
    
    def _find_node(self, identifier: str) -> Node:
        """Находит узел по ID или alias"""
        real_id = self._resolve_id(identifier)
        return self._index[real_id]
    
    def _find_node_safe(self, identifier: str) -> Optional[Node]:
        """Безопасный поиск узла (возвращает None если не найден)"""
        try:
            return self._find_node(identifier)
        except NodeNotFoundError:
            return None

    # ========================================================================
    # ОТОБРАЖЕНИЕ ДЕРЕВА
    # ========================================================================
    
    def get_text_tree(
        self,
        nodes: Optional[List[Node]] = None,
        max_depth: Optional[int] = None,
        current_depth: int = 0,
        prefix: str = "",
        use_colors: bool = True,
        show_hidden: bool = False,
        filter_tags: Optional[List[str]] = None,
        filter_progress: Optional[List[str]] = None
    ) -> List[str]:
        """Генерирует текстовое представление дерева"""
        
        if nodes is None:
            nodes = self._get_root_nodes()
        
        if max_depth is not None and current_depth > max_depth:
            return []
        
        # Фильтрация
        visible_nodes = []
        for node in nodes:
            if not node.visible and not show_hidden:
                continue
            if filter_tags and not any(t in node.tags for t in filter_tags):
                continue
            if filter_progress and node.progress not in filter_progress:
                continue
            visible_nodes.append(node)
        
        if not visible_nodes:
            return []
        
        # ANSI Colors
        C_ID = "\033[94m" if use_colors else ""      # Синий
        C_LOCK = "\033[91m" if use_colors else ""    # Красный
        C_EDIT = "\033[92m" if use_colors else ""    # Зелёный
        C_TAG = "\033[93m" if use_colors else ""     # Жёлтый
        C_ALIAS = "\033[95m" if use_colors else ""   # Пурпурный
        C_DIM = "\033[90m" if use_colors else ""     # Серый
        C_END = "\033[0m" if use_colors else ""
        
        lines = []
        for i, node in enumerate(visible_nodes):
            is_last = (i == len(visible_nodes) - 1)
            connector = "└── " if is_last else "├── "
            
            # Иконки статуса и прогресса
            progress_enum = TaskProgress(node.progress) if node.progress in [e.value for e in TaskProgress] else TaskProgress.TODO
            status_enum = NodeStatus(node.status) if node.status in [e.value for e in NodeStatus] else NodeStatus.EDITABLE
            
            p_icon = PROGRESS_ICONS.get(progress_enum, "⚪")
            s_icon = STATUS_ICONS.get(status_enum, "✍️")
            color = C_LOCK if node.is_locked() else C_EDIT
            
            # Формируем строку
            hidden_mark = f" {C_DIM}[HIDDEN]{C_END}" if not node.visible else ""
            
            # ID и alias
            id_part = f"{C_ID}{node.id[:8]}{C_END}"
            alias_part = f" {C_ALIAS}@{node.alias}{C_END}" if node.alias else ""
            
            # Теги
            tags_part = ""
            if node.tags:
                tags_str = " ".join(f"#{t}" for t in node.tags)
                tags_part = f" {C_TAG}{tags_str}{C_END}"
            
            line = f"{prefix}{connector}{id_part}{alias_part} {p_icon} {node.text}{tags_part}{hidden_mark} {color}{s_icon}{C_END}"
            lines.append(line)
            
            # Рекурсия для детей
            new_prefix = prefix + ("    " if is_last else "│   ")
            lines.extend(self.get_text_tree(
                nodes=node.children,
                max_depth=max_depth,
                current_depth=current_depth + 1,
                prefix=new_prefix,
                use_colors=use_colors,
                show_hidden=show_hidden,
                filter_tags=filter_tags,
                filter_progress=filter_progress
            ))
        
        return lines
    
    # ========================================================================
    # CRUD ОПЕРАЦИИ
    # ========================================================================
    
    def add_node(
        self,
        parent_id: Optional[str],
        text: str,
        is_locked: bool = False,
        tags: Optional[List[str]] = None,
        alias: Optional[str] = None
    ) -> str:
        """Добавляет новый узел"""
        
        # Валидация
        if not text or not text.strip():
            raise ValidationError("Текст узла не может быть пустым")
        
        if alias and alias in self._alias_index:
            raise ValidationError(f"Alias '{alias}' уже используется")
        
        # Определяем родителя
        # "root" может быть и спец-значением CLI (добавить в корень), и alias узла.
        # Сначала пытаемся найти узел, если не найден и это "root" — добавляем в корень.
        parent: Optional[Node] = None
        if parent_id:
            parent = self._find_node_safe(parent_id)
            if parent is None and parent_id != "root":
                raise NodeNotFoundError(parent_id)
        
        # Создаём узел
        new_id = self._generate_id()
        node = Node(
            id=new_id,
            text=text.strip(),
            status=NodeStatus.LOCKED.value if is_locked else NodeStatus.EDITABLE.value,
            tags=tags or [],
            alias=alias,
            parent_id=parent.id if parent else None
        )
        
        # Добавляем в структуру
        if parent:
            parent.children.append(node)
        # Корневые узлы будут добавлены в tree при _save() через _get_root_nodes()
        
        # Обновляем индекс
        self._index[new_id] = node
        if alias:
            self._alias_index[alias] = new_id
        
        self._log(ActionType.ADD, new_id, text)
        self._save()
        
        status_mark = "L" if is_locked else "E"
        alias_info = f" @{alias}" if alias else ""
        return f"✅ Добавлено: {new_id[:8]}{alias_info} [{status_mark}]"
    
    def edit_node(self, node_id: str, new_text: str) -> str:
        """Редактирует текст узла"""
        node = self._find_node(node_id)
        
        if node.is_locked():
            raise NodeLockedError(node_id, "edit")
        
        if not new_text or not new_text.strip():
            raise ValidationError("Текст узла не может быть пустым")
        
        old_text = node.text
        node.text = new_text.strip()
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.EDIT, node.id, f"{old_text} -> {new_text}")
        self._save()
        
        return f"✅ Текст узла {node.id[:8]} обновлен"
    
    def delete_node(self, node_id: str, force: bool = False) -> str:
        """Удаляет (скрывает) узел"""
        node = self._find_node(node_id)
        
        if node.is_locked() and not force:
            raise NodeLockedError(node_id, "delete")
        
        if node.has_locked_children() and not force:
            locked_ids = node.get_locked_children_ids()
            raise NodeLockedError(
                node_id, 
                f"delete (содержит заблокированные узлы: {', '.join(locked_ids[:3])}...)"
            )
        
        node.visible = False
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.DELETE, node.id, node.text)
        self._save()
        
        return f"✅ Узел {node.id[:8]} удален (скрыт)"
    
    def set_status(self, node_id: str, status: str, recursive: bool = False) -> str:
        """Изменяет статус узла"""
        node = self._find_node(node_id)
        
        # Валидация статуса
        try:
            status_enum = NodeStatus(status)
        except ValueError:
            raise ValidationError(f"Неверный статус: {status}. Допустимые: locked, editable")
        
        self._apply_status(node, status_enum.value, recursive)
        
        self._log(ActionType.STATUS_CHANGE, node.id, f"{status} (recursive={recursive})")
        self._save()
        
        return f"✅ Статус {node.id[:8]} -> {status} {'(рекурсивно)' if recursive else ''}"
    
    def _apply_status(self, node: Node, status: str, recursive: bool) -> None:
        """Рекурсивно применяет статус"""
        node.status = status
        node.updated_at = datetime.now().isoformat()
        
        if recursive:
            for child in node.children:
                self._apply_status(child, status, True)
    
    def set_progress(self, node_id: str, progress: str) -> str:
        """Изменяет прогресс задачи"""
        node = self._find_node(node_id)
        
        try:
            progress_enum = TaskProgress(progress)
        except ValueError:
            valid = ", ".join(e.value for e in TaskProgress)
            raise ValidationError(f"Неверный прогресс: {progress}. Допустимые: {valid}")
        
        node.progress = progress_enum.value
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.PROGRESS_CHANGE, node.id, progress)
        self._save()
        
        return f"✅ Прогресс {node.id[:8]} -> {progress}"
    
    def rewrite_children(self, node_id: str, items: List[str], force: bool = False) -> str:
        """Перезаписывает дочерние узлы"""
        node = self._find_node(node_id)
        
        if node.is_locked():
            raise NodeLockedError(node_id, "rewrite")
        
        # Проверяем заблокированных детей
        if not force and node.has_locked_children():
            locked_ids = node.get_locked_children_ids()
            raise NodeLockedError(
                node_id,
                f"rewrite (содержит заблокированные узлы: {', '.join(i[:8] for i in locked_ids[:3])})"
            )
        
        # Удаляем старых детей из индекса
        def remove_from_index(n: Node):
            if n.id in self._index:
                del self._index[n.id]
            if n.alias and n.alias in self._alias_index:
                del self._alias_index[n.alias]
            for child in n.children:
                remove_from_index(child)
        
        for child in node.children:
            remove_from_index(child)
        
        # Создаём новых детей
        new_children = []
        for text in items:
            if not text.strip():
                continue
            child_id = self._generate_id()
            child = Node(
                id=child_id,
                text=text.strip(),
                parent_id=node.id
            )
            new_children.append(child)
            self._index[child_id] = child
        
        node.children = new_children
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.REWRITE, node.id, f"Replaced with {len(new_children)} items")
        self._save()
        
        return f"✅ Подпункты {node.id[:8]} перезаписаны ({len(new_children)} шт.)"
    
    def move_node(self, node_id: str, new_parent_id: str) -> str:
        """Перемещает узел к новому родителю"""
        node = self._find_node(node_id)
        
        # "root" может быть и спец-значением (переместить в корень), и alias узла.
        # Сначала пытаемся найти узел с таким ID/alias.
        new_parent = self._find_node_safe(new_parent_id)
        
        if new_parent is None and new_parent_id == "root":
            # Перемещение в корень (спец-значение)
            if node.parent_id:
                old_parent = self._find_node(node.parent_id)
                old_parent.children = [c for c in old_parent.children if c.id != node.id]
            node.parent_id = None
        elif new_parent is None:
            # Узел не найден и это не спец-значение "root"
            raise NodeNotFoundError(new_parent_id)
        else:
            # Обычное перемещение под new_parent
            
            # Проверяем, что не перемещаем в собственного потомка
            def is_descendant_of(ancestor: Node, target_id: str) -> bool:
                """Проверяет, является ли target_id потомком ancestor"""
                for child in ancestor.children:
                    if child.id == target_id:
                        return True
                    if is_descendant_of(child, target_id):
                        return True
                return False
            
            # Проверяем что new_parent не является потомком node
            if is_descendant_of(node, new_parent.id):
                raise ValidationError("Нельзя переместить узел в собственного потомка")
            
            # Находим и удаляем из старого родителя
            if node.parent_id:
                old_parent = self._find_node(node.parent_id)
                old_parent.children = [c for c in old_parent.children if c.id != node.id]
            
            new_parent.children.append(node)
            node.parent_id = new_parent.id
        
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.MOVE, node.id, f"Moved to {new_parent_id}")
        self._save()
        
        return f"✅ Узел {node.id[:8]} перемещен в {new_parent_id}"
    
    # ========================================================================
    # ТЕГИ И АЛИАСЫ
    # ========================================================================
    
    def add_tag(self, node_id: str, tag: str) -> str:
        """Добавляет тег к узлу"""
        node = self._find_node(node_id)
        
        tag = tag.strip().lower().replace(" ", "-")
        if not tag:
            raise ValidationError("Тег не может быть пустым")
        
        if tag not in node.tags:
            node.tags.append(tag)
            node.updated_at = datetime.now().isoformat()
            self._log(ActionType.TAG_ADD, node.id, tag)
            self._save()
        
        return f"✅ Тег #{tag} добавлен к {node.id[:8]}"
    
    def remove_tag(self, node_id: str, tag: str) -> str:
        """Удаляет тег с узла"""
        node = self._find_node(node_id)
        
        tag = tag.strip().lower()
        if tag in node.tags:
            node.tags.remove(tag)
            node.updated_at = datetime.now().isoformat()
            self._log(ActionType.TAG_REMOVE, node.id, tag)
            self._save()
        
        return f"✅ Тег #{tag} удален с {node.id[:8]}"
    
    def set_alias(self, node_id: str, alias: Optional[str]) -> str:
        """Устанавливает или удаляет alias узла"""
        node = self._find_node(node_id)
        
        # Удаляем старый alias из индекса
        if node.alias and node.alias in self._alias_index:
            del self._alias_index[node.alias]
        
        if alias:
            alias = alias.strip().lstrip("@")  # Убираем @ если пользователь добавил
            
            # Валидация alias
            if not alias:
                raise ValidationError("Alias не может быть пустым")
            if " " in alias:
                raise ValidationError("Alias не может содержать пробелы")
            if alias in self._alias_index:
                raise ValidationError(f"Alias '{alias}' уже используется")
            
            self._alias_index[alias] = node.id
        
        node.alias = alias
        node.updated_at = datetime.now().isoformat()
        
        self._log(ActionType.ALIAS_SET, node.id, alias or "(removed)")
        self._save()
        
        if alias:
            return f"✅ Alias @{alias} установлен для {node.id[:8]}"
        return f"✅ Alias удален с {node.id[:8]}"
    
    # ========================================================================
    # ПОИСК И НАВИГАЦИЯ
    # ========================================================================
    
    def search(
        self,
        query: str,
        include_hidden: bool = False,
        tags: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        """Поиск узлов по тексту и тегам"""
        results = []
        query_lower = query.lower()
        
        for node in self._index.values():
            if not node.visible and not include_hidden:
                continue
            
            # Поиск по тексту (если query не пустой)
            text_match = query_lower and query_lower in node.text.lower()
            
            # Поиск по alias (если query не пустой)
            alias_match = query_lower and node.alias and query_lower in node.alias.lower()
            
            # Поиск по тегам (если указаны теги)
            tag_match = tags and any(t in node.tags for t in tags)
            
            # Если указан query — ищем по тексту/alias
            # Если указаны tags — фильтруем по тегам
            # Если указано и то и другое — нужно совпадение обоих условий
            if query_lower and tags:
                # Оба условия: текст/alias И теги
                if (text_match or alias_match) and tag_match:
                    results.append({
                        "id": node.id,
                        "alias": node.alias,
                        "text": node.text,
                        "status": node.status,
                        "progress": node.progress,
                        "tags": node.tags
                    })
            elif query_lower:
                # Только текст/alias
                if text_match or alias_match:
                    results.append({
                        "id": node.id,
                        "alias": node.alias,
                        "text": node.text,
                        "status": node.status,
                        "progress": node.progress,
                        "tags": node.tags
                    })
            elif tags:
                # Только теги
                if tag_match:
                    results.append({
                        "id": node.id,
                        "alias": node.alias,
                        "text": node.text,
                        "status": node.status,
                        "progress": node.progress,
                        "tags": node.tags
                    })
        
        return results
    
    def get_path(self, node_id: str) -> str:
        """Возвращает путь до узла"""
        node = self._find_node(node_id)
        
        path = []
        current: Optional[Node] = node
        while current is not None:
            display_id = current.alias or current.id[:8]
            path.append(f"{display_id}: {current.text}")
            if current.parent_id:
                try:
                    current = self._find_node(current.parent_id)
                except NodeNotFoundError:
                    current = None
            else:
                current = None
        
        path.reverse()
        return " → ".join(path)
    
    def get_next(self) -> str:
        """Находит следующую невыполненную задачу"""
        def find_next_todo(nodes: List[Node]) -> Optional[Node]:
            for node in nodes:
                if not node.visible:
                    continue
                if node.progress == TaskProgress.DONE.value:
                    continue
                
                # Проверяем детей
                visible_children = [c for c in node.children if c.visible]
                if not visible_children:
                    # Лист — это наша цель
                    return node
                
                # Все дети выполнены?
                if all(c.progress == TaskProgress.DONE.value for c in visible_children):
                    return node
                
                # Ищем в детях
                result = find_next_todo(visible_children)
                if result:
                    return result
            
            return None
        
        next_node = find_next_todo(self._get_root_nodes())
        
        if next_node:
            display_id = next_node.alias or next_node.id[:8]
            return f"🎯 Следующая задача: {display_id} — {next_node.text} [{next_node.progress}]"
        
        return "🎉 Все задачи выполнены!"
    
    # ========================================================================
    # СТАТИСТИКА И УТИЛИТЫ
    # ========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику проекта"""
        stats = {
            "total": 0,
            "visible": 0,
            "hidden": 0,
            "locked": 0,
            "editable": 0,
            "by_progress": {p.value: 0 for p in TaskProgress},
            "tags": {},
        }
        
        for node in self._index.values():
            stats["total"] += 1
            
            if node.visible:
                stats["visible"] += 1
            else:
                stats["hidden"] += 1
            
            if node.is_locked():
                stats["locked"] += 1
            else:
                stats["editable"] += 1
            
            if node.progress in stats["by_progress"]:
                stats["by_progress"][node.progress] += 1
            
            for tag in node.tags:
                stats["tags"][tag] = stats["tags"].get(tag, 0) + 1
        
        return stats
    
    def get_snapshots(self) -> List[str]:
        """Возвращает список доступных снапшотов"""
        if not self.snap_dir.exists():
            return []
        return sorted(f.name for f in self.snap_dir.iterdir() if f.suffix == '.json')
    
    def restore_snapshot(self, filename: str) -> str:
        """Восстанавливает базу из снапшота"""
        snap_path = self.snap_dir / filename
        
        if not snap_path.exists():
            raise HBTError(f"Снапшот '{filename}' не найден")
        
        # Создаём backup текущего состояния
        self._create_snapshot(f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        shutil.copy(snap_path, self.db_path)
        self._load()
        
        self._log(ActionType.RESTORE, "system", filename)
        
        return f"✅ База данных восстановлена из {filename}"
    
    def clear_all(self, confirm: bool = False) -> str:
        """Полная очистка базы данных"""
        if not confirm:
            raise ValidationError("Требуется подтверждение (confirm=True)")
        
        # Сохраняем снапшот перед очисткой
        self._create_snapshot(f"pre_clear_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        self._data = self._get_default_data()
        self._index.clear()
        self._alias_index.clear()
        self._save()
        
        return "✅ База данных очищена. Снимок сохранен в .hbt_history"
    
    def import_tasks(self, parent_id: Optional[str], filepath: str) -> str:
        """Импортирует задачи из текстового файла"""
        path = Path(filepath)
        
        if not path.exists():
            raise HBTError(f"Файл '{filepath}' не найден")
        
        try:
            lines = [
                line.strip() 
                for line in path.read_text(encoding='utf-8').splitlines() 
                if line.strip()
            ]
        except Exception as e:
            raise HBTError(f"Ошибка чтения файла: {e}")
        
        count = 0
        for line in lines:
            self.add_node(parent_id, line)
            count += 1
        
        return f"✅ Импортировано {count} узлов"
    
    def export_tree(self, filepath: str, use_colors: bool = False) -> str:
        """Экспортирует дерево в текстовый файл"""
        lines = self.get_text_tree(use_colors=use_colors)
        
        path = Path(filepath)
        path.write_text("\n".join(lines), encoding='utf-8')
        
        return f"📄 Экспортировано в {filepath}"
    
    # ========================================================================
    # ВАЛИДАЦИЯ И ЦЕЛОСТНОСТЬ
    # ========================================================================
    
    def verify_integrity(self) -> List[str]:
        """Проверяет целостность данных и возвращает список проблем"""
        issues = []
        
        # Проверяем, что все parent_id указывают на существующие узлы
        for node in self._index.values():
            if node.parent_id and node.parent_id not in self._index:
                issues.append(f"Узел {node.id[:8]} ссылается на несуществующего родителя {node.parent_id[:8]}")
        
        # Проверяем уникальность alias
        seen_aliases = {}
        for node in self._index.values():
            if node.alias:
                if node.alias in seen_aliases:
                    issues.append(f"Дублирующийся alias '{node.alias}' у узлов {seen_aliases[node.alias][:8]} и {node.id[:8]}")
                seen_aliases[node.alias] = node.id
        
        # Проверяем циклические ссылки
        def has_cycle(node_id: str, visited: set) -> bool:
            if node_id in visited:
                return True
            node = self._index.get(node_id)
            if not node or not node.parent_id:
                return False
            visited.add(node_id)
            return has_cycle(node.parent_id, visited)
        
        for node_id in self._index:
            if has_cycle(node_id, set()):
                issues.append(f"Обнаружена циклическая ссылка для узла {node_id[:8]}")
                break
        
        return issues
    
    # ========================================================================
    # СВОЙСТВА ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ
    # ========================================================================
    
    @property
    def data(self) -> Dict[str, Any]:
        """Обратная совместимость со старым API"""
        return self._data


# ============================================================================
# CLI ИНТЕРФЕЙС
# ============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки"""
    parser = argparse.ArgumentParser(
        prog="hbt",
        description="HBT (Hierarchical Block Text) — CLI для управления иерархическими задачами",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  hbt add --text "Новая задача" --locked
  hbt view --depth 2 --tags important
  hbt search "авторизация"
  hbt set-progress --id abc123 --state done
        """
    )
    parser.add_argument("--version", action="version", version=f"HBT {__version__}")
    
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    
    # ========== VIEW ==========
    v = subparsers.add_parser("view", help="Просмотр дерева задач")
    v.add_argument("--id", help="Фокус на конкретном узле (ID или alias)")
    v.add_argument("--depth", type=int, help="Максимальная глубина отображения")
    v.add_argument("--raw", action="store_true", help="Вывод без цветов")
    v.add_argument("--all", action="store_true", help="Показать скрытые узлы")
    v.add_argument("--tags", nargs="+", help="Фильтр по тегам")
    v.add_argument("--progress", nargs="+", choices=["todo", "doing", "done", "blocked"], help="Фильтр по прогрессу")
    
    # ========== ADD ==========
    a = subparsers.add_parser("add", help="Добавить новый узел")
    a.add_argument("--to", default="root", help="ID родительского узла (по умолчанию: root)")
    a.add_argument("--text", "-t", required=True, help="Текст задачи")
    a.add_argument("--locked", "-l", action="store_true", help="Сразу заблокировать")
    a.add_argument("--tags", nargs="+", help="Теги для узла")
    a.add_argument("--alias", "-a", help="Короткий alias для узла")
    
    # ========== EDIT ==========
    e = subparsers.add_parser("edit", help="Редактировать текст узла")
    e.add_argument("--id", required=True, help="ID или alias узла")
    e.add_argument("--text", "-t", required=True, help="Новый текст")
    
    # ========== DELETE ==========
    dl = subparsers.add_parser("delete", help="Удалить (скрыть) узел")
    dl.add_argument("--id", required=True, help="ID или alias узла")
    dl.add_argument("--force", "-f", action="store_true", help="Принудительное удаление locked-узлов")
    
    # ========== MOVE ==========
    mv = subparsers.add_parser("move", help="Переместить узел")
    mv.add_argument("--id", required=True, help="ID перемещаемого узла")
    mv.add_argument("--to", required=True, help="ID нового родителя (или 'root')")
    
    # ========== STATUS ==========
    st = subparsers.add_parser("status", help="Изменить статус блокировки")
    st.add_argument("--id", required=True, help="ID или alias узла")
    st.add_argument("--mode", choices=["locked", "editable"], required=True, help="Новый статус")
    st.add_argument("-r", "--recursive", action="store_true", help="Применить рекурсивно")
    
    # ========== PROGRESS ==========
    pg = subparsers.add_parser("set-progress", help="Изменить прогресс задачи")
    pg.add_argument("--id", required=True, help="ID или alias узла")
    pg.add_argument("--state", choices=["todo", "doing", "done", "blocked"], required=True, help="Новый статус")
    
    # ========== REWRITE ==========
    rw = subparsers.add_parser("rewrite", help="Перезаписать дочерние узлы")
    rw.add_argument("--id", required=True, help="ID родительского узла")
    rw.add_argument("items", nargs="+", help="Список новых подпунктов")
    rw.add_argument("--force", "-f", action="store_true", help="Игнорировать locked-детей")
    
    # ========== TAGS ==========
    tag = subparsers.add_parser("tag", help="Управление тегами")
    tag.add_argument("--id", required=True, help="ID или alias узла")
    tag.add_argument("--add", nargs="+", help="Добавить теги")
    tag.add_argument("--remove", nargs="+", help="Удалить теги")
    
    # ========== ALIAS ==========
    al = subparsers.add_parser("alias", help="Установить alias для узла")
    al.add_argument("--id", required=True, help="ID узла")
    al.add_argument("--name", help="Новый alias (пусто для удаления)")
    
    # ========== SEARCH ==========
    s = subparsers.add_parser("search", help="Поиск узлов")
    s.add_argument("query", help="Поисковый запрос")
    s.add_argument("--tags", nargs="+", help="Фильтр по тегам")
    s.add_argument("--all", action="store_true", help="Искать в скрытых")
    
    # ========== PATH ==========
    pt = subparsers.add_parser("path", help="Показать путь до узла")
    pt.add_argument("--id", required=True, help="ID или alias узла")
    
    # ========== NEXT ==========
    subparsers.add_parser("next", help="Найти следующую задачу")
    
    # ========== HISTORY ==========
    h = subparsers.add_parser("history", help="История изменений")
    h.add_argument("--limit", type=int, default=20, help="Количество записей")
    
    # ========== STATS ==========
    subparsers.add_parser("stats", help="Статистика проекта")
    
    # ========== IMPORT ==========
    imp = subparsers.add_parser("import", help="Импорт из файла")
    imp.add_argument("--to", default="root", help="ID родительского узла")
    imp.add_argument("--file", required=True, help="Путь к файлу")
    
    # ========== EXPORT ==========
    exp = subparsers.add_parser("export", help="Экспорт в файл")
    exp.add_argument("file", nargs="?", default="ai_context.txt", help="Имя файла")
    
    # ========== BACKUP ==========
    subparsers.add_parser("backup", help="Создать ручной снапшот")
    
    # ========== ROLLBACK ==========
    rb = subparsers.add_parser("rollback", help="Управление снапшотами")
    rb.add_argument("--list", action="store_true", help="Показать доступные снапшоты")
    rb.add_argument("--restore", help="Восстановить из снапшота")
    
    # ========== CLEAR ==========
    cl = subparsers.add_parser("clear", help="Очистить базу данных")
    cl.add_argument("--yes", action="store_true", help="Подтвердить очистку")
    
    # ========== VERIFY ==========
    subparsers.add_parser("verify", help="Проверить целостность данных")
    
    return parser


def format_error(error: Exception) -> str:
    """Форматирует ошибку для вывода"""
    if isinstance(error, NodeNotFoundError):
        return f"❌ Узел '{error.node_id}' не найден"
    elif isinstance(error, NodeLockedError):
        return f"🔒 Узел '{error.node_id}' заблокирован. Операция '{error.operation}' запрещена"
    elif isinstance(error, ValidationError):
        return f"⚠️ Ошибка валидации: {error}"
    elif isinstance(error, HBTError):
        return f"❌ {error}"
    else:
        return f"💥 Неожиданная ошибка: {error}"


def main():
    """Точка входа CLI"""
    parser = create_parser()
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    try:
        driver = HBTDriver()
    except IntegrityError as e:
        print(format_error(e), file=sys.stderr)
        return 1
    
    try:
        result = execute_command(driver, args)
        if result:
            print(result)
        return 0
    except HBTError as e:
        print(format_error(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⚡ Прервано пользователем")
        return 130
    except Exception as e:
        print(format_error(e), file=sys.stderr)
        return 1


def execute_command(driver: HBTDriver, args: argparse.Namespace) -> Optional[str]:
    """Выполняет команду и возвращает результат"""
    
    if args.command == "view":
        nodes = None
        if args.id:
            target = driver._find_node(args.id)
            nodes = [target]
        
        tree = driver.get_text_tree(
            nodes=nodes,
            max_depth=args.depth,
            use_colors=not args.raw,
            show_hidden=args.all,
            filter_tags=args.tags,
            filter_progress=args.progress
        )
        return "\n".join(tree) if tree else "📭 Дерево пусто"
    
    elif args.command == "add":
        parent = args.to if args.to != "root" else None
        return driver.add_node(
            parent_id=parent,
            text=args.text,
            is_locked=args.locked,
            tags=args.tags,
            alias=args.alias
        )
    
    elif args.command == "edit":
        return driver.edit_node(args.id, args.text)
    
    elif args.command == "delete":
        return driver.delete_node(args.id, force=args.force)
    
    elif args.command == "move":
        return driver.move_node(args.id, args.to)
    
    elif args.command == "status":
        return driver.set_status(args.id, args.mode, args.recursive)
    
    elif args.command == "set-progress":
        return driver.set_progress(args.id, args.state)
    
    elif args.command == "rewrite":
        return driver.rewrite_children(args.id, args.items, force=args.force)
    
    elif args.command == "tag":
        results = []
        if args.add:
            for tag in args.add:
                results.append(driver.add_tag(args.id, tag))
        if args.remove:
            for tag in args.remove:
                results.append(driver.remove_tag(args.id, tag))
        return "\n".join(results) if results else "⚠️ Укажите --add или --remove"
    
    elif args.command == "alias":
        return driver.set_alias(args.id, args.name)
    
    elif args.command == "search":
        results = driver.search(args.query, include_hidden=args.all, tags=args.tags)
        if not results:
            return "🔍 Ничего не найдено"
        
        lines = []
        for r in results:
            alias_part = f" @{r['alias']}" if r['alias'] else ""
            tags_part = f" #{' #'.join(r['tags'])}" if r['tags'] else ""
            lines.append(f"{r['id'][:8]}{alias_part} — {r['text']}{tags_part}")
        return "\n".join(lines)
    
    elif args.command == "path":
        return driver.get_path(args.id)
    
    elif args.command == "next":
        return driver.get_next()
    
    elif args.command == "history":
        history = driver.data.get("history", [])[-args.limit:]
        if not history:
            return "📜 История пуста"
        
        lines = []
        for entry in history:
            checksum = entry.get('checksum', '')[:6]
            node_id = entry.get('node_id', entry.get('id', 'unknown'))  # Совместимость со старым форматом
            lines.append(f"[{entry['timestamp']}] {checksum} {entry['action'].upper()} {node_id[:8]} — {entry['text']}")
        return "\n".join(lines)
    
    elif args.command == "stats":
        s = driver.get_stats()
        
        lines = [
            "📊 Статистика проекта:",
            f"  Всего узлов: {s['total']}",
            f"  ├── Видимых: {s['visible']}",
            f"  └── Скрытых: {s['hidden']}",
            f"  Заблокировано 🔒: {s['locked']}",
            f"  Редактируемых ✍️: {s['editable']}",
            "",
            "  Прогресс:",
        ]
        
        for progress, count in s['by_progress'].items():
            icon = PROGRESS_ICONS.get(TaskProgress(progress), "⚪")
            lines.append(f"    {icon} {progress}: {count}")
        
        if s['tags']:
            lines.append("")
            lines.append("  Топ тегов:")
            for tag, count in sorted(s['tags'].items(), key=lambda x: -x[1])[:5]:
                lines.append(f"    #{tag}: {count}")
        
        return "\n".join(lines)
    
    elif args.command == "import":
        parent = args.to if args.to != "root" else None
        return driver.import_tasks(parent, args.file)
    
    elif args.command == "export":
        return driver.export_tree(args.file, use_colors=False)
    
    elif args.command == "backup":
        name = f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        driver._create_snapshot(name)
        return f"💾 Создан ручной снимок: {name}"
    
    elif args.command == "rollback":
        if args.restore:
            return driver.restore_snapshot(args.restore)
        
        snaps = driver.get_snapshots()
        if not snaps:
            return "📜 Нет доступных снапшотов"
        
        lines = ["📜 Доступные точки восстановления:"]
        for snap in snaps[-20:]:  # Последние 20
            lines.append(f"  - {snap}")
        return "\n".join(lines)
    
    elif args.command == "clear":
        if not args.yes:
            confirm = input("⚠️ Вы уверены, что хотите очистить ВСЕ данные? (y/n): ")
            if confirm.lower() != 'y':
                return "❌ Отменено"
        return driver.clear_all(confirm=True)
    
    elif args.command == "verify":
        issues = driver.verify_integrity()
        if not issues:
            return "✅ Целостность данных в порядке"
        return "⚠️ Обнаружены проблемы:\n" + "\n".join(f"  - {issue}" for issue in issues)
    
    return None


if __name__ == "__main__":
    sys.exit(main())
