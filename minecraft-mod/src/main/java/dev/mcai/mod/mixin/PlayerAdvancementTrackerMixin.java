package dev.mcai.mod.mixin;

import dev.mcai.mod.McaiMod;
import net.minecraft.advancement.AdvancementEntry;
import net.minecraft.advancement.AdvancementProgress;
import net.minecraft.advancement.PlayerAdvancementTracker;
import net.minecraft.server.network.ServerPlayerEntity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Reports a completed advancement (the moment its last criterion is granted) to the automation engine. */
@Mixin(PlayerAdvancementTracker.class)
public abstract class PlayerAdvancementTrackerMixin {
    @Shadow
    private ServerPlayerEntity owner;

    @Shadow
    public abstract AdvancementProgress getProgress(AdvancementEntry advancement);

    @Inject(method = "grantCriterion", at = @At("RETURN"))
    private void mcai$onGrant(AdvancementEntry advancement, String criterionName, CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValueZ() && getProgress(advancement).isDone()) {
            McaiMod.onAdvancement(owner, advancement);
        }
    }
}
