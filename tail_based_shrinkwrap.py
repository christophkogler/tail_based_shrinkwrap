bl_info = {
    "name": "Tail-Based Shrinkwrap for Bones",
    "author": "Christoph Kogler",
    "version": (1, 0, 0),
    "blender": (4, 3, 2),
    "location": "View3D > Sidebar (N Panel) > Tail Shrinkwrap",
    "description": "Non-destructive, tail-based shrinkwrap for bones using empties and constraints.",
    "warning": "",
    "wiki_url": "",
    "category": "Object",
}

import bpy

class OBJECT_OT_setup_tail_shrinkwrap(bpy.types.Operator):
    """Non-destructively setup tail-based shrinkwrap for selected bones."""
    bl_idname = "object.setup_tail_shrinkwrap"
    bl_label = "Setup Tail Shrinkwrap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object is not an armature.")
            return {'CANCELLED'}
        
        if context.mode not in {'POSE', 'EDIT_ARMATURE'}:
            self.report({'ERROR'}, "Please switch to Pose or Edit Armature mode to select bones.")
            return {'CANCELLED'}

        # --- Helper Functions ---
        def get_or_create_collections(context, arm_obj):
            main_collection_name = "Tail Shrinkwrap"
            if main_collection_name in bpy.data.collections:
                bone_targets_collection = bpy.data.collections[main_collection_name]
            else:
                bone_targets_collection = bpy.data.collections.new(main_collection_name)
                context.scene.collection.children.link(bone_targets_collection)
            rig_name = arm_obj.name
            rig_collection = None
            for coll in bone_targets_collection.children:
                if coll.name == rig_name:
                    rig_collection = coll
                    break
            if rig_collection is None:
                rig_collection = bpy.data.collections.new(rig_name)
                bone_targets_collection.children.link(rig_collection)
            return bone_targets_collection, rig_collection

        def gather_bone_data(context, arm_obj):
            selected_bones = {}
            bone_tail_data = {}
            bone_lengths = {}
            if context.mode == 'POSE':
                for pb in context.selected_pose_bones:
                    parent_name = pb.parent.name if pb.parent else None
                    tail_world = arm_obj.matrix_world @ pb.tail
                    bone_tail_data[pb.name] = (tail_world, parent_name)
                    selected_bones[pb.name] = parent_name
                    bone_lengths[pb.name] = (pb.tail - pb.head).length
            elif context.mode == 'EDIT_ARMATURE':
                for eb in arm_obj.data.edit_bones:
                    if eb.select:
                        parent_name = eb.parent.name if eb.parent else None
                        tail_world = arm_obj.matrix_world @ eb.tail
                        bone_tail_data[eb.name] = (tail_world, parent_name)
                        selected_bones[eb.name] = parent_name
                        bone_lengths[eb.name] = (eb.tail - eb.head).length
            return selected_bones, bone_tail_data, bone_lengths

        def get_chain_root(bone, selected_bones):
            while selected_bones.get(bone):
                parent = selected_bones.get(bone)
                if parent not in selected_bones or parent is None:
                    break
                bone = parent
            return bone

        def create_empties(context, arm_obj, bone_tail_data, bone_lengths, selected_bones, rig_collection):
            chain_to_collection = {}
            bone_to_empty = {}
            bpy.ops.object.mode_set(mode='OBJECT')
            for bone_name, (tail_pos, parent_name) in bone_tail_data.items():
                bpy.ops.object.empty_add(type='SINGLE_ARROW', location=tail_pos)
                empty_obj = context.active_object
                empty_obj.name = "Empty_" + bone_name
                # Scale the empty relative to the bone's length.
                empty_scale = bone_lengths.get(bone_name, 1.0) * 1.5
                empty_obj.scale = (empty_scale, empty_scale, empty_scale)
                
                # Get or create a collection for the bone chain.
                chain_root = get_chain_root(bone_name, selected_bones)
                if chain_root not in chain_to_collection:
                    chain_coll_name = "Chain_" + chain_root
                    new_coll = bpy.data.collections.new(chain_coll_name)
                    rig_collection.children.link(new_coll)
                    chain_to_collection[chain_root] = new_coll

                # Link the empty to the chain collection.
                chain_to_collection[chain_root].objects.link(empty_obj)
                try:
                    context.scene.collection.objects.unlink(empty_obj)
                except Exception:
                    pass

                bone_to_empty[bone_name] = empty_obj

                # Add a Child Of constraint if the bone has a parent.
                if parent_name:
                    childof_constraint = empty_obj.constraints.new(type='CHILD_OF')
                    childof_constraint.name = "ChildOf_" + parent_name
                    childof_constraint.target = arm_obj
                    childof_constraint.subtarget = parent_name
                    context.view_layer.update()
            return bone_to_empty

        def add_limit_distance_constraints(context, bone_tail_data, bone_to_empty):
            for bone_name, (tail_pos, parent_name) in bone_tail_data.items():
                child_empty = bone_to_empty[bone_name]
                ld_constraint = child_empty.constraints.new(type='LIMIT_DISTANCE')
                if parent_name and parent_name in bone_to_empty:
                    ld_constraint.target = bone_to_empty[parent_name]
                    ld_constraint.name = "LimitDist_" + parent_name
                else:
                    child_empty.constraints.remove(ld_constraint)
                    continue
                ld_constraint.limit_mode = 'LIMITDIST_ONSURFACE'
                ld_constraint.head_tail = 1  # use target's tail
                ld_constraint.distance = 0.0
                context.view_layer.update()

        def add_shrinkwrap_constraints(context, bone_to_empty, bone_lengths, arm_obj, mesh_target):
            for bone_name, empty_obj in bone_to_empty.items():
                sw_constraint = empty_obj.constraints.new(type='SHRINKWRAP')
                sw_constraint.name = "Shrinkwrap_" + bone_name
                if mesh_target:
                    sw_constraint.target = mesh_target
                sw_constraint.shrinkwrap_type = 'TARGET_PROJECT'
                sw_constraint.track_axis = 'TRACK_Z'
                sw_constraint.use_track_normal = True
                sw_constraint.wrap_mode = 'OUTSIDE_SURFACE'
                sw_constraint.distance = bone_lengths.get(bone_name, 1.0) * context.scene.shrinkwrap_distance_scale

                # Add a driver to toggle the influence based on the armature's custom property.
                drv = sw_constraint.driver_add("influence").driver
                drv.expression = "1.0 if var else 0.0"
                var = drv.variables.new()
                var.name = "var"
                var.targets[0].id = arm_obj
                var.targets[0].data_path = '["Shrinkwrap_Fingers"]'

        def add_pose_constraints(context, bone_to_empty, arm_obj):
            bpy.ops.object.select_all(action='DESELECT')
            context.view_layer.objects.active = arm_obj
            bpy.ops.object.mode_set(mode='POSE')
            for bone_name, empty_obj in bone_to_empty.items():
                pb = arm_obj.pose.bones.get(bone_name)
                if pb is not None:
                    # Add IK constraint.
                    ik_constraint = pb.constraints.new(type='IK')
                    ik_constraint.name = "IK_" + bone_name
                    ik_constraint.target = empty_obj
                    ik_constraint.chain_count = 1  # Adjust chain length as needed.

                    # Optionally add Damped Track constraint.
                    if context.scene.use_damped_track:
                        if "Damped_Track_Influence" not in arm_obj:
                            arm_obj["Damped_Track_Influence"] = 1.0
                        dt_constraint = pb.constraints.new(type='DAMPED_TRACK')
                        dt_constraint.name = "DampedTrack_" + bone_name
                        dt_constraint.target = empty_obj
                        dt_constraint.track_axis = context.scene.damped_track_axis

                        drv = dt_constraint.driver_add("influence").driver
                        drv.expression = "var"
                        var = drv.variables.new()
                        var.name = "var"
                        var.targets[0].id = arm_obj
                        var.targets[0].data_path = '["Damped_Track_Influence"]'

        # --- End Helper Functions ---

        # Ensure the custom property exists.
        if "Shrinkwrap_Fingers" not in arm_obj.keys():
            arm_obj["Shrinkwrap_Fingers"] = False

        # Create/get the necessary collections.
        bone_targets_collection, rig_collection = get_or_create_collections(context, arm_obj)
        # Gather data on selected bones.
        selected_bones, bone_tail_data, bone_lengths = gather_bone_data(context, arm_obj)

        if not bone_tail_data:
            self.report({'ERROR'}, "No bones selected.")
            return {'CANCELLED'}

        # First pass: create empties.
        bone_to_empty = create_empties(context, arm_obj, bone_tail_data, bone_lengths, selected_bones, rig_collection)
        # Second pass: add Limit Distance constraints.
        add_limit_distance_constraints(context, bone_tail_data, bone_to_empty)
        # Third pass: add Shrinkwrap constraints with drivers.
        mesh_target = context.scene.shrinkwrap_mesh_target  # Renamed from 'my_mesh_target'
        add_shrinkwrap_constraints(context, bone_to_empty, bone_lengths, arm_obj, mesh_target)
        # Finally, add IK (and optionally Damped Track) constraints.
        add_pose_constraints(context, bone_to_empty, arm_obj)

        self.report({'INFO'}, "Tail-based Shrinkwrap setup complete.")
        return {'FINISHED'}


class OBJECT_OT_bake_tail_shrinkwrap(bpy.types.Operator):
    """Bake bones currently controlled by the Tail Shrinkwrap empties (for the current frame range)."""
    bl_idname = "object.bake_tail_shrinkwrap"
    bl_label = "Bake Tail Shrinkwrap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # (Same code as OBJECT_OT_bake_bones, just renamed)
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object is not an armature.")
            return {'CANCELLED'}
        
        main_coll = bpy.data.collections.get("Tail Shrinkwrap")
        if not main_coll:
            self.report({'ERROR'}, "Tail Shrinkwrap collection not found.")
            return {'CANCELLED'}
        
        rig_coll = None
        for coll in main_coll.children:
            if coll.name == arm_obj.name:
                rig_coll = coll
                break
        if rig_coll is None:
            self.report({'ERROR'}, "No Tail Shrinkwrap collection found for the active armature.")
            return {'CANCELLED'}
        
        bones_to_bake = set()
        for chain_coll in rig_coll.children:
            for obj in chain_coll.objects:
                if obj.type == 'EMPTY' and obj.name.startswith("Empty_"):
                    bone_name = obj.name[6:]
                    bones_to_bake.add(bone_name)
        
        if not bones_to_bake:
            self.report({'ERROR'}, "No bones found to bake from the Tail Shrinkwrap hierarchy.")
            return {'CANCELLED'}
        
        bpy.ops.object.mode_set(mode='POSE')
        for bone in arm_obj.data.bones:
            bone.select = False
        for pb in arm_obj.pose.bones:
            if pb.name in bones_to_bake:
                pb.bone.select = True

        scene = context.scene
        frame_start = scene.frame_start
        frame_end = scene.frame_end
        bpy.ops.nla.bake(
            frame_start=frame_start,
            frame_end=frame_end,
            only_selected=True,
            visual_keying=True,
            clear_constraints=True,
            use_current_action=True
        )
        
        self.report({'INFO'}, "Tail Shrinkwrap bones baked successfully.")
        return {'FINISHED'}


class OBJECT_OT_clear_tail_shrinkwrap(bpy.types.Operator):
    """Apply current pose and remove all Tail Shrinkwrap empties & constraints from the selected armature."""
    bl_idname = "object.clear_tail_shrinkwrap"
    bl_label = "Clear Tail Shrinkwrap"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # (Same code as OBJECT_OT_clear_empties, just renamed)
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object is not an armature.")
            return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='POSE')
        for bone in arm_obj.data.bones:
            bone.select = True

        bpy.ops.pose.visual_transform_apply()

        bones_to_clear = set()
        main_coll = bpy.data.collections.get("Tail Shrinkwrap")
        if main_coll:
            rig_coll = None
            for coll in main_coll.children:
                if coll.name == arm_obj.name:
                    rig_coll = coll
                    break
            if rig_coll:
                for chain_coll in rig_coll.children:
                    for obj in chain_coll.objects:
                        if obj.type == 'EMPTY' and obj.name.startswith("Empty_"):
                            bones_to_clear.add(obj.name[6:])
        
        # Only clear constraints on the bones in the shrinkwrap hierarchy
        for bone_name in bones_to_clear:
            pb = arm_obj.pose.bones.get(bone_name)
            if pb:
                for constraint in pb.constraints[:]:
                    if constraint.name.startswith("IK_") or constraint.name.startswith("DampedTrack_"):
                        pb.constraints.remove(constraint)
        
        # Remove empties
        if main_coll and rig_coll:
            empties_to_remove = []
            for chain_coll in rig_coll.children:
                for obj in chain_coll.objects:
                    if obj.type == 'EMPTY' and obj.name.startswith("Empty_"):
                        empties_to_remove.append(obj)
            for obj in empties_to_remove:
                for coll in obj.users_collection:
                    coll.objects.unlink(obj)
                bpy.data.objects.remove(obj)
            
            # Remove empty chain subcollections
            for chain_coll in list(rig_coll.children):
                if not chain_coll.objects:
                    rig_coll.children.unlink(chain_coll)
                    bpy.data.collections.remove(chain_coll)
            
            # Remove the rig collection if empty
            if not rig_coll.objects and not rig_coll.children:
                main_coll.children.unlink(rig_coll)
                bpy.data.collections.remove(rig_coll)

        self.report({'INFO'}, "Tail Shrinkwrap cleared. Pose applied and empties removed.")
        return {'FINISHED'}


class VIEW3D_PT_tail_shrinkwrap_panel(bpy.types.Panel):
    """Tail Shrinkwrap tools for bones in the 3D View sidebar."""
    bl_label = "Tail Shrinkwrap Tools"
    bl_idname = "VIEW3D_PT_tail_shrinkwrap_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tail Shrinkwrap"

    def draw(self, context):
        layout = self.layout
        
        layout.label(text="1) Select Shrinkwrap Mesh Target")
        layout.prop(context.scene, "shrinkwrap_mesh_target")  # Renamed from 'my_mesh_target'
        layout.separator()
        
        layout.label(text="2) Select Bones & Settings (Pose/Edit mode)")
        layout.prop(context.scene, "shrinkwrap_distance_scale")
        layout.prop(context.scene, "use_damped_track")
        if context.scene.use_damped_track:
            layout.prop(context.scene, "damped_track_axis")
        layout.separator()
        
        layout.label(text="3) Setup Tail Shrinkwrap")
        layout.operator("object.setup_tail_shrinkwrap", text="Setup Tail Shrinkwrap")
        layout.separator()
        
        layout.label(text="Other")
        layout.operator("object.bake_tail_shrinkwrap", text="Bake Tail Shrinkwrap")
        layout.operator("object.clear_tail_shrinkwrap", text="Clear Tail Shrinkwrap")
        layout.separator()


classes = (
    OBJECT_OT_setup_tail_shrinkwrap,
    OBJECT_OT_bake_tail_shrinkwrap,
    OBJECT_OT_clear_tail_shrinkwrap,
    VIEW3D_PT_tail_shrinkwrap_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.shrinkwrap_mesh_target = bpy.props.PointerProperty(
        name="Shrinkwrap Mesh Target",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        description="Select the mesh object to use as the shrinkwrap target"
    )
    bpy.types.Scene.shrinkwrap_distance_scale = bpy.props.FloatProperty(
        name="Shrinkwrap Distance Scale",
        default=0.75,
        min=0.0,
        description="Multiplier for bone length to determine the initial shrinkwrap distance"
    )
    bpy.types.Scene.use_damped_track = bpy.props.BoolProperty(
        name="Damped Track",
        default=False,
        description="Enable Damped Track constraint on bones"
    )
    bpy.types.Scene.damped_track_axis = bpy.props.EnumProperty(
        name="Damped Track Axis",
        description="Axis for the Damped Track constraint",
        items=[
            ('TRACK_X', "X", "Track along X axis"),
            ('TRACK_NEGATIVE_X', "-X", "Track along -X axis"),
            ('TRACK_Y', "Y", "Track along Y axis"),
            ('TRACK_NEGATIVE_Y', "-Y", "Track along -Y axis"),
            ('TRACK_Z', "Z", "Track along Z axis"),
            ('TRACK_NEGATIVE_Z', "-Z", "Track along -Z axis"),
        ],
        default='TRACK_X'
    )

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.shrinkwrap_mesh_target
    del bpy.types.Scene.shrinkwrap_distance_scale
    del bpy.types.Scene.use_damped_track
    del bpy.types.Scene.damped_track_axis

if __name__ == "__main__":
    register()
