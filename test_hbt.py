#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты для HBT (Hierarchical Block Text)

Запуск: python3 test_hbt.py
Или:    python3 -m pytest test_hbt.py -v

Copyright (c) 2026 Максим Кузьминский (Maxim Kuzminsky)
Email: i@m-letto.ru
"""

import unittest
import tempfile
import shutil
import os
import json
from pathlib import Path

# Импортируем тестируемый модуль
from hbt import (
    HBTDriver,
    Node,
    NodeStatus,
    TaskProgress,
    HBTError,
    NodeNotFoundError,
    NodeLockedError,
    ValidationError,
    IntegrityError,
    __version__
)


class TestHBTBase(unittest.TestCase):
    """Базовый класс для тестов с настройкой временной директории"""
    
    def setUp(self):
        """Создаём временную директорию для каждого теста"""
        self.test_dir = tempfile.mkdtemp()
        self.original_dir = os.getcwd()
        os.chdir(self.test_dir)
        
    def tearDown(self):
        """Очищаем временную директорию после теста"""
        os.chdir(self.original_dir)
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def create_driver(self) -> HBTDriver:
        """Создаёт новый экземпляр драйвера"""
        return HBTDriver()


class TestNodeModel(unittest.TestCase):
    """Тесты модели Node"""
    
    def test_node_creation(self):
        """Тест создания узла"""
        node = Node(id="test123", text="Test node")
        self.assertEqual(node.id, "test123")
        self.assertEqual(node.text, "Test node")
        self.assertEqual(node.status, "editable")
        self.assertEqual(node.progress, "todo")
        self.assertTrue(node.visible)
        self.assertEqual(node.tags, [])
        self.assertIsNone(node.alias)
        
    def test_node_is_locked(self):
        """Тест проверки блокировки"""
        node = Node(id="test123", text="Test", status="locked")
        self.assertTrue(node.is_locked())
        
        node2 = Node(id="test456", text="Test", status="editable")
        self.assertFalse(node2.is_locked())
    
    def test_node_to_dict(self):
        """Тест сериализации узла"""
        node = Node(
            id="test123",
            text="Test node",
            tags=["tag1", "tag2"],
            alias="test"
        )
        data = node.to_dict()
        
        self.assertEqual(data["id"], "test123")
        self.assertEqual(data["text"], "Test node")
        self.assertEqual(data["tags"], ["tag1", "tag2"])
        self.assertEqual(data["alias"], "test")
    
    def test_node_from_dict(self):
        """Тест десериализации узла"""
        data = {
            "id": "test123",
            "text": "Test node",
            "status": "locked",
            "progress": "doing",
            "visible": True,
            "tags": ["tag1"],
            "alias": "test",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "parent_id": None,
            "children": []
        }
        node = Node.from_dict(data)
        
        self.assertEqual(node.id, "test123")
        self.assertEqual(node.text, "Test node")
        self.assertEqual(node.status, "locked")
        self.assertEqual(node.progress, "doing")
    
    def test_node_has_locked_children(self):
        """Тест проверки заблокированных детей"""
        child1 = Node(id="child1", text="Child 1", status="editable")
        child2 = Node(id="child2", text="Child 2", status="locked")
        parent = Node(id="parent", text="Parent", children=[child1, child2])
        
        self.assertTrue(parent.has_locked_children())
        
        child2.status = "editable"
        self.assertFalse(parent.has_locked_children())


class TestHBTDriverBasic(TestHBTBase):
    """Базовые тесты драйвера"""
    
    def test_driver_initialization(self):
        """Тест инициализации драйвера"""
        driver = self.create_driver()
        self.assertIsNotNone(driver)
        self.assertIsNotNone(driver._data)
        
    def test_empty_tree(self):
        """Тест пустого дерева"""
        driver = self.create_driver()
        tree = driver.get_text_tree()
        self.assertEqual(tree, [])
    
    def test_version(self):
        """Тест версии"""
        self.assertEqual(__version__, "1.0.0")


class TestAddNode(TestHBTBase):
    """Тесты добавления узлов"""
    
    def test_add_root_node(self):
        """Тест добавления корневого узла"""
        driver = self.create_driver()
        result = driver.add_node(None, "Root task")
        
        self.assertIn("✅ Добавлено", result)
        self.assertEqual(len(driver._index), 1)
    
    def test_add_child_node(self):
        """Тест добавления дочернего узла"""
        driver = self.create_driver()
        driver.add_node(None, "Parent")
        parent_id = list(driver._index.keys())[0]
        
        driver.add_node(parent_id, "Child")
        self.assertEqual(len(driver._index), 2)
        
        parent = driver._find_node(parent_id)
        self.assertEqual(len(parent.children), 1)
    
    def test_add_node_with_alias(self):
        """Тест добавления узла с alias"""
        driver = self.create_driver()
        result = driver.add_node(None, "Task", alias="mytask")
        
        self.assertIn("@mytask", result)
        node = driver._find_node("mytask")
        self.assertEqual(node.alias, "mytask")
    
    def test_add_node_with_tags(self):
        """Тест добавления узла с тегами"""
        driver = self.create_driver()
        driver.add_node(None, "Task", tags=["tag1", "tag2"])
        
        node = list(driver._index.values())[0]
        self.assertEqual(node.tags, ["tag1", "tag2"])
    
    def test_add_locked_node(self):
        """Тест добавления заблокированного узла"""
        driver = self.create_driver()
        result = driver.add_node(None, "Locked task", is_locked=True)
        
        self.assertIn("[L]", result)
        node = list(driver._index.values())[0]
        self.assertTrue(node.is_locked())
    
    def test_add_node_empty_text_fails(self):
        """Тест: пустой текст вызывает ошибку"""
        driver = self.create_driver()
        
        with self.assertRaises(ValidationError):
            driver.add_node(None, "")
        
        with self.assertRaises(ValidationError):
            driver.add_node(None, "   ")
    
    def test_add_node_duplicate_alias_fails(self):
        """Тест: дублирующийся alias вызывает ошибку"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", alias="task")
        
        with self.assertRaises(ValidationError):
            driver.add_node(None, "Task 2", alias="task")


class TestEditNode(TestHBTBase):
    """Тесты редактирования узлов"""
    
    def test_edit_node(self):
        """Тест редактирования текста"""
        driver = self.create_driver()
        driver.add_node(None, "Original text", alias="task")
        
        result = driver.edit_node("task", "New text")
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertEqual(node.text, "New text")
    
    def test_edit_locked_node_fails(self):
        """Тест: редактирование заблокированного узла запрещено"""
        driver = self.create_driver()
        driver.add_node(None, "Locked", alias="task", is_locked=True)
        
        with self.assertRaises(NodeLockedError):
            driver.edit_node("task", "New text")
    
    def test_edit_node_empty_text_fails(self):
        """Тест: пустой текст при редактировании запрещён"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        with self.assertRaises(ValidationError):
            driver.edit_node("task", "")


class TestDeleteNode(TestHBTBase):
    """Тесты удаления узлов"""
    
    def test_delete_node(self):
        """Тест удаления (скрытия) узла"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        result = driver.delete_node("task")
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertFalse(node.visible)
    
    def test_delete_locked_node_fails(self):
        """Тест: удаление заблокированного узла запрещено"""
        driver = self.create_driver()
        driver.add_node(None, "Locked", alias="task", is_locked=True)
        
        with self.assertRaises(NodeLockedError):
            driver.delete_node("task")
    
    def test_delete_locked_node_with_force(self):
        """Тест: принудительное удаление заблокированного узла"""
        driver = self.create_driver()
        driver.add_node(None, "Locked", alias="task", is_locked=True)
        
        result = driver.delete_node("task", force=True)
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertFalse(node.visible)
    
    def test_delete_node_with_locked_children_fails(self):
        """Тест: удаление узла с заблокированными детьми запрещено"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Child", alias="child", is_locked=True)
        
        with self.assertRaises(NodeLockedError):
            driver.delete_node("parent")


class TestStatusAndProgress(TestHBTBase):
    """Тесты статусов и прогресса"""
    
    def test_set_status_locked(self):
        """Тест установки статуса locked"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        result = driver.set_status("task", "locked")
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertTrue(node.is_locked())
    
    def test_set_status_recursive(self):
        """Тест рекурсивной установки статуса"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Child 1")
        driver.add_node("parent", "Child 2")
        
        driver.set_status("parent", "locked", recursive=True)
        
        parent = driver._find_node("parent")
        self.assertTrue(parent.is_locked())
        for child in parent.children:
            self.assertTrue(child.is_locked())
    
    def test_set_progress(self):
        """Тест установки прогресса"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        for progress in ["todo", "doing", "done", "blocked"]:
            result = driver.set_progress("task", progress)
            self.assertIn("✅", result)
            node = driver._find_node("task")
            self.assertEqual(node.progress, progress)
    
    def test_set_invalid_progress_fails(self):
        """Тест: неверный прогресс вызывает ошибку"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        with self.assertRaises(ValidationError):
            driver.set_progress("task", "invalid")


class TestRewrite(TestHBTBase):
    """Тесты перезаписи детей"""
    
    def test_rewrite_children(self):
        """Тест перезаписи дочерних узлов"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Old child 1")
        driver.add_node("parent", "Old child 2")
        
        result = driver.rewrite_children("parent", ["New 1", "New 2", "New 3"])
        
        self.assertIn("✅", result)
        self.assertIn("3 шт", result)
        
        parent = driver._find_node("parent")
        self.assertEqual(len(parent.children), 3)
        self.assertEqual(parent.children[0].text, "New 1")
    
    def test_rewrite_locked_node_fails(self):
        """Тест: перезапись заблокированного узла запрещена"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent", is_locked=True)
        
        with self.assertRaises(NodeLockedError):
            driver.rewrite_children("parent", ["New"])
    
    def test_rewrite_with_locked_children_fails(self):
        """Тест: перезапись узла с заблокированными детьми запрещена"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Child", alias="child", is_locked=True)
        
        with self.assertRaises(NodeLockedError):
            driver.rewrite_children("parent", ["New"])


class TestMoveNode(TestHBTBase):
    """Тесты перемещения узлов"""
    
    def test_move_node(self):
        """Тест перемещения узла"""
        driver = self.create_driver()
        driver.add_node(None, "Parent 1", alias="p1")
        driver.add_node(None, "Parent 2", alias="p2")
        driver.add_node("p1", "Child", alias="child")
        
        result = driver.move_node("child", "p2")
        
        self.assertIn("✅", result)
        
        p1 = driver._find_node("p1")
        p2 = driver._find_node("p2")
        self.assertEqual(len(p1.children), 0)
        self.assertEqual(len(p2.children), 1)
    
    def test_move_to_root(self):
        """Тест перемещения в корень"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Child", alias="child")
        
        driver.move_node("child", "root")
        
        child = driver._find_node("child")
        self.assertIsNone(child.parent_id)
    
    def test_move_to_descendant_fails(self):
        """Тест: перемещение в собственного потомка запрещено"""
        driver = self.create_driver()
        driver.add_node(None, "Parent", alias="parent")
        driver.add_node("parent", "Child", alias="child")
        
        with self.assertRaises(ValidationError):
            driver.move_node("parent", "child")


class TestTagsAndAlias(TestHBTBase):
    """Тесты тегов и алиасов"""
    
    def test_add_tag(self):
        """Тест добавления тега"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task")
        
        result = driver.add_tag("task", "important")
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertIn("important", node.tags)
    
    def test_remove_tag(self):
        """Тест удаления тега"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="task", tags=["tag1", "tag2"])
        
        result = driver.remove_tag("task", "tag1")
        
        self.assertIn("✅", result)
        node = driver._find_node("task")
        self.assertNotIn("tag1", node.tags)
        self.assertIn("tag2", node.tags)
    
    def test_set_alias(self):
        """Тест установки alias"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        node_id = list(driver._index.keys())[0]
        
        result = driver.set_alias(node_id, "newalias")
        
        self.assertIn("✅", result)
        node = driver._find_node("newalias")
        self.assertEqual(node.alias, "newalias")
    
    def test_set_duplicate_alias_fails(self):
        """Тест: дублирующийся alias запрещён"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", alias="task1")
        driver.add_node(None, "Task 2", alias="task2")
        
        with self.assertRaises(ValidationError):
            driver.set_alias("task2", "task1")


class TestSearch(TestHBTBase):
    """Тесты поиска"""
    
    def test_search_by_text(self):
        """Тест поиска по тексту"""
        driver = self.create_driver()
        driver.add_node(None, "Authentication module")
        driver.add_node(None, "Database layer")
        driver.add_node(None, "Auth helpers")
        
        results = driver.search("auth")
        
        self.assertEqual(len(results), 2)
    
    def test_search_by_tags(self):
        """Тест поиска по тегам"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", tags=["backend"])
        driver.add_node(None, "Task 2", tags=["frontend"])
        driver.add_node(None, "Task 3", tags=["backend", "api"])
        
        results = driver.search("", tags=["backend"])
        
        self.assertEqual(len(results), 2)
    
    def test_search_hidden_nodes(self):
        """Тест поиска скрытых узлов"""
        driver = self.create_driver()
        driver.add_node(None, "Visible task", alias="visible")
        driver.add_node(None, "Hidden task", alias="hidden")
        driver.delete_node("hidden")
        
        results_without_hidden = driver.search("task")
        results_with_hidden = driver.search("task", include_hidden=True)
        
        self.assertEqual(len(results_without_hidden), 1)
        self.assertEqual(len(results_with_hidden), 2)


class TestNavigation(TestHBTBase):
    """Тесты навигации"""
    
    def test_get_path(self):
        """Тест получения пути"""
        driver = self.create_driver()
        driver.add_node(None, "Root", alias="root")
        driver.add_node("root", "Level 1", alias="l1")
        driver.add_node("l1", "Level 2", alias="l2")
        
        # Проверяем что дочерние узлы находятся в children родителя
        root_node = driver._find_node("root")
        self.assertEqual(len(root_node.children), 1, "Root должен иметь 1 ребёнка")
        self.assertEqual(root_node.children[0].text, "Level 1")
        
        l1_node = driver._find_node("l1")
        self.assertEqual(len(l1_node.children), 1, "L1 должен иметь 1 ребёнка")
        self.assertEqual(l1_node.children[0].text, "Level 2")
        
        # Проверяем путь для l2 — должен содержать все уровни
        path = driver.get_path("l2")
        
        # Путь должен содержать все уровни и разделитель
        self.assertIn("Level 2", path)
        self.assertIn("Level 1", path) 
        self.assertIn("Root", path)
        self.assertIn("→", path)
        
        # Проверяем порядок (Root должен быть первым)
        self.assertTrue(path.index("Root") < path.index("Level 1"), 
                       f"Root должен быть перед Level 1 в пути: {path}")
        self.assertTrue(path.index("Level 1") < path.index("Level 2"),
                       f"Level 1 должен быть перед Level 2 в пути: {path}")
    
    def test_get_next(self):
        """Тест получения следующей задачи"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", alias="t1")
        driver.add_node(None, "Task 2", alias="t2")
        driver.set_progress("t1", "done")
        
        result = driver.get_next()
        
        self.assertIn("Task 2", result)
    
    def test_get_next_all_done(self):
        """Тест: все задачи выполнены"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", alias="t1")
        driver.set_progress("t1", "done")
        
        result = driver.get_next()
        
        self.assertIn("Все задачи выполнены", result)


class TestStats(TestHBTBase):
    """Тесты статистики"""
    
    def test_get_stats(self):
        """Тест получения статистики"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", tags=["backend"], is_locked=True)
        driver.add_node(None, "Task 2", tags=["backend", "api"])
        driver.add_node(None, "Task 3", alias="t3")
        driver.set_progress("t3", "done")
        driver.delete_node("t3")
        
        stats = driver.get_stats()
        
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["visible"], 2)
        self.assertEqual(stats["hidden"], 1)
        self.assertEqual(stats["locked"], 1)
        self.assertEqual(stats["editable"], 2)
        self.assertEqual(stats["by_progress"]["done"], 1)
        self.assertEqual(stats["tags"]["backend"], 2)


class TestBackupAndRestore(TestHBTBase):
    """Тесты резервного копирования"""
    
    def test_create_snapshot(self):
        """Тест создания снапшота"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        
        snap_name = driver._create_snapshot("test_snap.json")
        
        self.assertEqual(snap_name, "test_snap.json")
        self.assertTrue((Path(self.test_dir) / ".hbt_history" / "test_snap.json").exists())
    
    def test_get_snapshots(self):
        """Тест получения списка снапшотов"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1")
        driver._create_snapshot("snap1.json")
        driver.add_node(None, "Task 2")
        driver._create_snapshot("snap2.json")
        
        snaps = driver.get_snapshots()
        
        self.assertIn("snap1.json", snaps)
        self.assertIn("snap2.json", snaps)
    
    def test_restore_snapshot(self):
        """Тест восстановления из снапшота"""
        driver = self.create_driver()
        driver.add_node(None, "Original task", alias="task")
        driver._create_snapshot("backup.json")
        
        driver.edit_node("task", "Modified task")
        self.assertEqual(driver._find_node("task").text, "Modified task")
        
        driver.restore_snapshot("backup.json")
        self.assertEqual(driver._find_node("task").text, "Original task")


class TestVerifyIntegrity(TestHBTBase):
    """Тесты проверки целостности"""
    
    def test_verify_clean_data(self):
        """Тест проверки чистых данных"""
        driver = self.create_driver()
        driver.add_node(None, "Task 1", alias="t1")
        driver.add_node("t1", "Task 2", alias="t2")
        
        issues = driver.verify_integrity()
        
        self.assertEqual(len(issues), 0)


class TestClearAll(TestHBTBase):
    """Тесты очистки"""
    
    def test_clear_without_confirm_fails(self):
        """Тест: очистка без подтверждения запрещена"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        
        with self.assertRaises(ValidationError):
            driver.clear_all(confirm=False)
    
    def test_clear_with_confirm(self):
        """Тест очистки с подтверждением"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        
        result = driver.clear_all(confirm=True)
        
        self.assertIn("✅", result)
        self.assertEqual(len(driver._index), 0)


class TestImportExport(TestHBTBase):
    """Тесты импорта/экспорта"""
    
    def test_import_tasks(self):
        """Тест импорта задач из файла"""
        driver = self.create_driver()
        
        # Создаём файл для импорта
        import_file = Path(self.test_dir) / "import.txt"
        import_file.write_text("Task 1\nTask 2\nTask 3\n")
        
        result = driver.import_tasks(None, str(import_file))
        
        self.assertIn("3", result)
        self.assertEqual(len(driver._index), 3)
    
    def test_export_tree(self):
        """Тест экспорта дерева"""
        driver = self.create_driver()
        driver.add_node(None, "Root task", alias="root")
        driver.add_node("root", "Child task")
        
        export_file = Path(self.test_dir) / "export.txt"
        result = driver.export_tree(str(export_file))
        
        # Проверяем что результат содержит либо ✅ либо 📄
        self.assertTrue("✅" in result or "📄" in result)
        self.assertTrue(export_file.exists())
        
        content = export_file.read_text()
        self.assertIn("Root task", content)
        self.assertIn("Child task", content)


class TestTextTree(TestHBTBase):
    """Тесты отображения дерева"""
    
    def test_get_text_tree(self):
        """Тест генерации текстового дерева"""
        driver = self.create_driver()
        driver.add_node(None, "Root", alias="root")
        driver.add_node("root", "Child 1")
        driver.add_node("root", "Child 2")
        
        tree = driver.get_text_tree(use_colors=False)
        
        self.assertTrue(len(tree) > 0)
        tree_text = "\n".join(tree)
        self.assertIn("Root", tree_text)
        self.assertIn("Child 1", tree_text)
        self.assertIn("Child 2", tree_text)
    
    def test_get_text_tree_with_depth(self):
        """Тест ограничения глубины"""
        driver = self.create_driver()
        driver.add_node(None, "Level 0", alias="l0")
        driver.add_node("l0", "Level 1", alias="l1")
        driver.add_node("l1", "Level 2")
        
        tree = driver.get_text_tree(max_depth=1, use_colors=False)
        tree_text = "\n".join(tree)
        
        self.assertIn("Level 0", tree_text)
        self.assertIn("Level 1", tree_text)
        self.assertNotIn("Level 2", tree_text)
    
    def test_get_text_tree_filter_tags(self):
        """Тест фильтрации по тегам"""
        driver = self.create_driver()
        driver.add_node(None, "Backend task", tags=["backend"])
        driver.add_node(None, "Frontend task", tags=["frontend"])
        
        tree = driver.get_text_tree(filter_tags=["backend"], use_colors=False)
        tree_text = "\n".join(tree)
        
        self.assertIn("Backend task", tree_text)
        self.assertNotIn("Frontend task", tree_text)
    
    def test_get_text_tree_filter_progress(self):
        """Тест фильтрации по прогрессу"""
        driver = self.create_driver()
        driver.add_node(None, "Todo task", alias="todo")
        driver.add_node(None, "Done task", alias="done")
        driver.set_progress("done", "done")
        
        tree = driver.get_text_tree(filter_progress=["todo"], use_colors=False)
        tree_text = "\n".join(tree)
        
        self.assertIn("Todo task", tree_text)
        self.assertNotIn("Done task", tree_text)


class TestNodeNotFound(TestHBTBase):
    """Тесты обработки несуществующих узлов"""
    
    def test_find_nonexistent_node(self):
        """Тест поиска несуществующего узла"""
        driver = self.create_driver()
        
        with self.assertRaises(NodeNotFoundError):
            driver._find_node("nonexistent")
    
    def test_edit_nonexistent_node(self):
        """Тест редактирования несуществующего узла"""
        driver = self.create_driver()
        
        with self.assertRaises(NodeNotFoundError):
            driver.edit_node("nonexistent", "text")


class TestResolveId(TestHBTBase):
    """Тесты разрешения ID"""
    
    def test_resolve_by_id(self):
        """Тест разрешения по ID"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        node_id = list(driver._index.keys())[0]
        
        resolved = driver._resolve_id(node_id)
        
        self.assertEqual(resolved, node_id)
    
    def test_resolve_by_alias(self):
        """Тест разрешения по alias"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="mytask")
        node_id = list(driver._index.keys())[0]
        
        resolved = driver._resolve_id("mytask")
        
        self.assertEqual(resolved, node_id)
    
    def test_resolve_with_at_prefix(self):
        """Тест разрешения с префиксом @"""
        driver = self.create_driver()
        driver.add_node(None, "Task", alias="mytask")
        node_id = list(driver._index.keys())[0]
        
        resolved = driver._resolve_id("@mytask")
        
        self.assertEqual(resolved, node_id)


class TestFilePersistence(TestHBTBase):
    """Тесты сохранения в файл"""
    
    def test_data_persists(self):
        """Тест сохранения данных между сессиями"""
        driver1 = self.create_driver()
        driver1.add_node(None, "Persistent task", alias="task")
        
        # Создаём новый драйвер (имитация перезапуска)
        driver2 = self.create_driver()
        
        node = driver2._find_node("task")
        self.assertEqual(node.text, "Persistent task")
    
    def test_tasks_json_created(self):
        """Тест создания файла tasks.json"""
        driver = self.create_driver()
        driver.add_node(None, "Task")
        
        self.assertTrue((Path(self.test_dir) / "tasks.json").exists())


# ============================================================================
# ЗАПУСК ТЕСТОВ
# ============================================================================

def run_tests():
    """Запуск всех тестов с подробным выводом"""
    print("=" * 70)
    print("HBT (Hierarchical Block Text) — Тестирование")
    print(f"Версия: {__version__}")
    print("=" * 70)
    print()
    
    # Создаём загрузчик тестов
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Добавляем все тестовые классы
    test_classes = [
        TestNodeModel,
        TestHBTDriverBasic,
        TestAddNode,
        TestEditNode,
        TestDeleteNode,
        TestStatusAndProgress,
        TestRewrite,
        TestMoveNode,
        TestTagsAndAlias,
        TestSearch,
        TestNavigation,
        TestStats,
        TestBackupAndRestore,
        TestVerifyIntegrity,
        TestClearAll,
        TestImportExport,
        TestTextTree,
        TestNodeNotFound,
        TestResolveId,
        TestFilePersistence,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # Запускаем тесты
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Итоговый отчёт
    print()
    print("=" * 70)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 70)
    print(f"Всего тестов: {result.testsRun}")
    print(f"✅ Успешно: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"❌ Провалено: {len(result.failures)}")
    print(f"💥 Ошибок: {len(result.errors)}")
    print()
    
    if result.wasSuccessful():
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
        print("   Код работает корректно и готов к использованию.")
    else:
        print("⚠️  ЕСТЬ ПРОБЛЕМЫ!")
        if result.failures:
            print("\nПровалившиеся тесты:")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if result.errors:
            print("\nТесты с ошибками:")
            for test, traceback in result.errors:
                print(f"  - {test}")
    
    print("=" * 70)
    
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    exit(run_tests())
